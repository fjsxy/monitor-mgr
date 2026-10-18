// Agent卷历史只保存已读到的UID关系，不按名字认领没有原owner链的残留卷。
package control

import (
	"context"
	"encoding/json"
	"net/url"
	"sort"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// agentStorageSchema追加真实v10存储关系，保留父资源消失后的原PVC身份。
var agentStorageSchema = []string{
	`CREATE TABLE agent_volume_identities(instance_id TEXT NOT NULL REFERENCES instances(id),agent_uid TEXT NOT NULL,pvc_uid TEXT NOT NULL,namespace TEXT NOT NULL,name TEXT NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,PRIMARY KEY(instance_id,pvc_uid))`,
	`CREATE TABLE agent_volume_lineage(instance_id TEXT NOT NULL,pvc_uid TEXT NOT NULL,st_uid TEXT NOT NULL,st_name TEXT NOT NULL,pod_uid TEXT NOT NULL,pod_name TEXT NOT NULL,PRIMARY KEY(instance_id,pvc_uid,st_uid,pod_uid),FOREIGN KEY(instance_id,pvc_uid) REFERENCES agent_volume_identities(instance_id,pvc_uid))`,
}

type agentVolumeLink struct{ pvcUID, name, stUID, stName, podUID, podName string }
type agentVolumeHistory struct{ uid, name, namespace string }

// agentVolumeHistory仅返回原Agent UID的可信记录，不把历史成功时间作为当前读取成功。
func (s *Store) agentVolumeHistory(ctx context.Context, i *rpc.ObservationInstance) ([]agentVolumeHistory, error) {
	if i.ReadOnly {
		return nil, nil
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT pvc_uid,name,namespace FROM agent_volume_identities WHERE instance_id=? AND agent_uid=?`, i.Id, i.Uid)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []agentVolumeHistory{}
	for rows.Next() {
		var h agentVolumeHistory
		if e = rows.Scan(&h.uid, &h.name, &h.namespace); e != nil {
			return nil, e
		}
		out = append(out, h)
	}
	return out, rows.Err()
}

// rememberAgentVolumes在全部列表成功后原子记录身份与父链，未知/不完整读取不增信。
func (s *Store) rememberAgentVolumes(ctx context.Context, i *rpc.ObservationInstance, links []agentVolumeLink) error {
	if i.ReadOnly || len(links) == 0 {
		return nil
	}
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	var current string
	if e = tx.QueryRowContext(ctx, `SELECT uid FROM instances WHERE id=?`, i.Id).Scan(&current); e != nil {
		return e
	}
	if current != i.Uid {
		return Error("CONFLICT", "保存卷身份时原Agent UID已变化")
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	for _, l := range links {
		if _, e = tx.ExecContext(ctx, `INSERT INTO agent_volume_identities VALUES(?,?,?,?,?,?,?) ON CONFLICT(instance_id,pvc_uid) DO UPDATE SET last_seen_at=excluded.last_seen_at`, i.Id, i.Uid, l.pvcUID, i.TargetNamespace, l.name, now, now); e != nil {
			return e
		}
		if _, e = tx.ExecContext(ctx, `INSERT OR IGNORE INTO agent_volume_lineage VALUES(?,?,?,?,?,?)`, i.Id, l.pvcUID, l.stUID, l.stName, l.podUID, l.podName); e != nil {
			return e
		}
	}
	return tx.Commit()
}

// observeAgentVolumes使用完整ST/Pod/PVC集合；Ready与真实volumeMount共同证明当前挂载。
func (s *Store) observeAgentVolumes(ctx context.Context, i *rpc.ObservationInstance, t *Target) ([]*rpc.VolumeObservation, error) {
	prefix := "/api/v1/namespaces/" + url.PathEscape(i.TargetNamespace)
	sts, e := t.listObjects(ctx, "/apis/apps/v1/namespaces/"+url.PathEscape(i.TargetNamespace)+"/statefulsets")
	if e != nil {
		return nil, e
	}
	owners := map[string]string{}
	for _, st := range sts {
		if agentOwnedBy(st, "PrometheusAgent", i.Uid) {
			m := objectMap(st, "metadata")
			uid, name := objectString(m, "uid"), objectString(m, "name")
			if uid == "" || name == "" {
				return nil, Error("TARGET_ERROR", "StatefulSet身份不完整")
			}
			owners[uid] = name
		}
	}
	pods, e := t.listObjects(ctx, prefix+"/pods")
	if e != nil {
		return nil, e
	}
	claims := map[string][]agentVolumeLink{}
	mounted := map[string][]string{}
	for _, pod := range pods {
		m := objectMap(pod, "metadata")
		parent := ownerUID(m)
		stName, owned := owners[parent]
		if !owned || !agentOwnedBy(pod, "StatefulSet", parent) {
			continue
		}
		name, uid := objectString(m, "name"), objectString(m, "uid")
		if name == "" || uid == "" {
			return nil, Error("TARGET_ERROR", "Pod身份不完整")
		}
		ready := false
		for _, raw := range anySlice(objectMap(pod, "status")["conditions"]) {
			c, _ := raw.(map[string]any)
			if c["type"] == "Ready" && c["status"] == "True" {
				ready = true
			}
		}
		mounts := map[string]bool{}
		for _, raw := range anySlice(objectMap(pod, "spec")["containers"]) {
			c, _ := raw.(map[string]any)
			for _, rawMount := range anySlice(c["volumeMounts"]) {
				v, _ := rawMount.(map[string]any)
				mounts[objectString(v, "name")] = true
			}
		}
		for _, raw := range anySlice(objectMap(pod, "spec")["volumes"]) {
			v, _ := raw.(map[string]any)
			claim := objectString(objectMap(v, "persistentVolumeClaim"), "claimName")
			if claim == "" {
				continue
			}
			claims[claim] = append(claims[claim], agentVolumeLink{name: claim, stUID: parent, stName: stName, podUID: uid, podName: name})
			if ready && mounts[objectString(v, "name")] {
				mounted[claim] = append(mounted[claim], name)
			}
		}
	}
	pvcs, e := t.listObjects(ctx, prefix+"/persistentvolumeclaims")
	if e != nil {
		return nil, e
	}
	history, e := s.agentVolumeHistory(ctx, i)
	if e != nil {
		return nil, e
	}
	byName := map[string]map[string]any{}
	for _, pvc := range pvcs {
		m := objectMap(pvc, "metadata")
		name, uid := objectString(m, "name"), objectString(m, "uid")
		if name == "" || uid == "" {
			return nil, Error("TARGET_ERROR", "PVC身份不完整")
		}
		byName[name] = pvc
	}
	confirmedAbsent, e := s.confirmedAbsentAgentVolumes(ctx, i)
	if e != nil {
		return nil, e
	}
	out := []*rpc.VolumeObservation{}
	links := []agentVolumeLink{}
	seen := map[string]bool{}
	for name, ls := range claims {
		pvc := byName[name]
		if pvc == nil {
			continue
		}
		uid := objectString(objectMap(pvc, "metadata"), "uid")
		for _, l := range ls {
			l.pvcUID = uid
			links = append(links, l)
		}
		out = append(out, agentVolumeObservation(i.TargetNamespace, pvc, mounted[name]))
		seen[uid] = true
	}
	for _, h := range history {
		if seen[h.uid] {
			continue
		}
		pvc := byName[h.name]
		// 关键：父已消失时仅原PVC UID可以延续记录；同名替换必须保持读取错误而不能自动接管。
		if pvc != nil && objectString(objectMap(pvc, "metadata"), "uid") != h.uid {
			// 关键：只有已独立核实原UID消失，且本次真实原owner链已采用新UID，才允许记录本实例新建卷。
			replacement := objectString(objectMap(pvc, "metadata"), "uid")
			if !confirmedAbsent[h.uid] || !seen[replacement] {
				return nil, Error("CONFLICT", "历史PVC名称出现新UID，禁止认领")
			}
			pvc = nil
		}
		if pvc != nil {
			out = append(out, agentVolumeObservation(h.namespace, pvc, []string{}))
		} else {
			out = append(out, &rpc.VolumeObservation{Namespace: h.namespace, Name: h.name, Uid: h.uid, Phase: "NotFound", AccessModes: []string{}, MountedByPods: []string{}})
		}
	}
	if e = s.rememberAgentVolumes(ctx, i, links); e != nil {
		return nil, e
	}
	sort.Slice(out, func(a, b int) bool { return out[a].Name+out[a].Uid < out[b].Name+out[b].Uid })
	return out, nil
}
func agentVolumeObservation(namespace string, pvc map[string]any, mounted []string) *rpc.VolumeObservation {
	meta, spec := objectMap(pvc, "metadata"), objectMap(pvc, "spec")
	access := []string{}
	for _, raw := range anySlice(spec["accessModes"]) {
		if v, ok := raw.(string); ok {
			access = append(access, v)
		}
	}
	if mounted == nil {
		mounted = []string{}
	}
	sort.Strings(mounted)
	return &rpc.VolumeObservation{Namespace: namespace, Name: objectString(meta, "name"), Uid: objectString(meta, "uid"), Phase: objectString(objectMap(pvc, "status"), "phase"), RequestedStorage: objectString(objectMap(objectMap(spec, "resources"), "requests"), "storage"), AccessModes: access, StorageClass: objectString(spec, "storageClassName"), MountedByPods: mounted}
}

// confirmedAbsentAgentVolumes仅认可持久只读核验的原UID消失，不按相同名称推断新旧卷。
func (s *Store) confirmedAbsentAgentVolumes(ctx context.Context, i *rpc.ObservationInstance) (map[string]bool, error) {
	out := map[string]bool{}
	if i.ReadOnly {
		return out, nil
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT volumes FROM agent_storage_tasks WHERE instance_id=? AND agent_uid=?`, i.Id, i.Uid)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	for rows.Next() {
		var raw []byte
		if e = rows.Scan(&raw); e != nil {
			return nil, e
		}
		var volumes []*rpc.VolumeObservation
		if e = json.Unmarshal(raw, &volumes); e != nil {
			return nil, Error("UNAVAILABLE", "卷核验历史不可读")
		}
		for _, v := range volumes {
			if v.GetRetentionResult_() == "Deleted" || v.GetRetentionResult_() == "Lost" {
				out[v.Uid] = true
			}
		}
	}
	return out, rows.Err()
}
