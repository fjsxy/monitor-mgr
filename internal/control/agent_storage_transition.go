// Agent危险变更核验实际ST策略与PVC owner UID；不修改存储或替Kubernetes完成GC。
package control

import (
	"context"
	"encoding/json"
	"net/url"
	"sort"
	"strconv"
	"strings"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

type agentAffectedVolume struct {
	Namespace, Name, UID, Policy, StatefulUID, StatefulName string
	Shard                                                   int32
}
type agentPolicyImpact struct{ UID, Name, Action, Policy string }
type agentStorageSnapshot struct {
	dangerous bool
	facts     []agentPolicyImpact
	mode      string
	policies  []agentStatefulPolicy
	volumes   []agentAffectedVolume
}

// agentCounts统一读取冻结JSON与当前CR的缺省计数，不丢弃合法零值。
func agentCounts(spec map[string]any) (int32, int32, error) {
	var normalized map[string]any
	if json.Unmarshal([]byte(canonical(spec)), &normalized) != nil {
		return 0, 0, Error("CONFLICT", "Agent计数不可读")
	}
	replicas, shards := int32(1), int32(1)
	if _, has := normalized["replicas"]; has {
		n, ok := number(normalized, "replicas")
		if !ok {
			return 0, 0, Error("CONFLICT", "实际Agent replicas无效")
		}
		replicas = n
	}
	if _, has := normalized["shards"]; has {
		n, ok := number(normalized, "shards")
		if !ok || n < 1 {
			return 0, 0, Error("CONFLICT", "实际Agent shards无效")
		}
		shards = n
	}
	return replicas, shards, nil
}
func agentShard(name, st string) (int32, error) {
	base := agentStatefulName(name, 0)
	if st == base {
		return 0, nil
	}
	if strings.HasPrefix(st, base+"-shard-") {
		n, e := strconv.ParseInt(strings.TrimPrefix(st, base+"-shard-"), 10, 32)
		if e == nil && n > 0 && agentStatefulName(name, int32(n)) == st {
			return int32(n), nil
		}
	}
	return 0, Error("CONFLICT", "原Agent StatefulSet派生身份无法确认")
}

// readAgentStorageTransition在事务与派发锁外完整读取；同份冻结spec驱动策略与影响判断。
func (s *Store) readAgentStorageTransition(ctx context.Context, i *rpc.ObservationInstance, t *Target, current map[string]any, desired map[string]any, deleting bool) (*agentStorageSnapshot, error) {
	if i.Component != AgentComponent {
		return nil, nil
	}
	mode, _ := agentStorageIdentity(desired)
	if deleting {
		mode, _ = agentStorageIdentity(objectMap(current, "spec"))
	}
	out := &agentStorageSnapshot{mode: mode, policies: []agentStatefulPolicy{}, volumes: []agentAffectedVolume{}}
	if mode == "" {
		return nil, Error("CONFLICT", "实际Agent存储模式无法确认")
	}
	nextReplicas, nextShards, e := agentCounts(desired)
	if e != nil {
		return nil, e
	}
	oldReplicas, oldShards, e := agentCounts(objectMap(current, "spec"))
	if e != nil {
		return nil, e
	}
	if current == nil {
		oldReplicas, oldShards = 0, 0
	}
	dangerous := deleting || current != nil && (nextReplicas < oldReplicas || nextShards < oldShards)
	out.dangerous = dangerous
	if mode == "emptyDir" {
		return out, nil
	}
	if current == nil {
		cfg := &AgentConfig{Name: i.Name, Namespace: i.TargetNamespace, Shards: nextShards, Storage: AgentStorage{Mode: "PVC"}}
		if nextReplicas > 0 {
			e = t.checkNewAgentClaims(ctx, cfg)
		}
		return out, e
	}
	scaled, deleted, e := agentRetention(objectMap(current, "spec"))
	if e != nil {
		return nil, e
	}
	ns := url.PathEscape(i.TargetNamespace)
	// 关键：先记录完整原UID链，后续父资源消失仍可核实Retain；失败不能当空资源放行。
	if _, e = s.observeAgentVolumes(ctx, i, t); e != nil {
		return nil, e
	}
	sts, e := t.listObjects(ctx, "/apis/apps/v1/namespaces/"+ns+"/statefulsets")
	if e != nil {
		return nil, e
	}
	pods, e := t.listObjects(ctx, "/api/v1/namespaces/"+ns+"/pods")
	if e != nil {
		return nil, e
	}
	pvcs, e := t.listObjects(ctx, "/api/v1/namespaces/"+ns+"/persistentvolumeclaims")
	if e != nil {
		return nil, e
	}
	stByUID := map[string]map[string]any{}
	affected := map[string]string{}
	shards := map[string]int32{}
	if dangerous && !deleting {
		a, b, err := agentRetention(desired)
		if err != nil {
			return nil, err
		}
		if a != scaled || b != deleted {
			return nil, Error("CONFLICT", "请先单独发布存储策略并核验实际StatefulSet，再挂起或缩减shards")
		}
	}
	for _, st := range sts {
		if !agentOwnedBy(st, "PrometheusAgent", i.Uid) {
			continue
		}
		meta := objectMap(st, "metadata")
		uid, name := objectString(meta, "uid"), objectString(meta, "name")
		if uid == "" || name == "" {
			return nil, Error("CONFLICT", "StatefulSet身份不完整")
		}
		shard, err := agentShard(i.Name, name)
		if err != nil {
			return nil, err
		}
		shards[uid] = shard
		stByUID[uid] = st
		a, b, err := agentRetention(objectMap(st, "spec"))
		if err != nil {
			return nil, err
		}
		out.policies = append(out.policies, agentStatefulPolicy{Name: name, UID: uid, WhenScaled: a, WhenDeleted: b})
		policy, action := "", ""
		if deleting || dangerous && shard >= nextShards {
			policy = deleted
			action = "whenDeleted"
			if b != deleted {
				return nil, Error("CONFLICT", "StatefulSet尚未采用whenDeleted，请先单独发布策略并确认")
			}
		} else if dangerous && nextReplicas < oldReplicas {
			policy = scaled
			action = "whenScaled"
			if a != scaled {
				return nil, Error("CONFLICT", "StatefulSet尚未采用whenScaled，请先单独发布策略并确认")
			}
		}
		if policy != "" {
			affected[uid] = policy
			out.facts = append(out.facts, agentPolicyImpact{UID: uid, Name: name, Action: action, Policy: policy})
		}
	}
	podOwner := map[string]string{}
	claimOwner := map[string]string{}
	for _, pod := range pods {
		meta := objectMap(pod, "metadata")
		owner := ownerUID(meta)
		if stByUID[owner] == nil || !agentOwnedBy(pod, "StatefulSet", owner) {
			continue
		}
		uid := objectString(meta, "uid")
		if uid == "" {
			return nil, Error("CONFLICT", "Pod身份不完整")
		}
		podOwner[uid] = owner
		for _, raw := range anySlice(objectMap(pod, "spec")["volumes"]) {
			v, _ := raw.(map[string]any)
			name := objectString(objectMap(v, "persistentVolumeClaim"), "claimName")
			if name != "" {
				if prior := claimOwner[name]; prior != "" && prior != owner {
					return nil, Error("CONFLICT", "PVC被多个StatefulSet引用")
				}
				claimOwner[name] = owner
			}
		}
	}
	// 已保存链只延续同原Agent UID，不从claimName或labels推断身份。
	history, e := s.agentVolumeHistory(ctx, i)
	if e != nil {
		return nil, e
	}
	trusted := map[string]map[string]bool{}
	for _, h := range history {
		if trusted[h.name] == nil {
			trusted[h.name] = map[string]bool{}
		}
		trusted[h.name][h.uid] = true
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT l.pvc_uid,l.st_uid,l.st_name,l.pod_uid FROM agent_volume_lineage l JOIN agent_volume_identities v ON v.instance_id=l.instance_id AND v.pvc_uid=l.pvc_uid WHERE l.instance_id=? AND v.agent_uid=?`, i.Id, i.Uid)
	if e != nil {
		return nil, e
	}
	type lineage struct{ pvc, st, name, pod string }
	lineages := []lineage{}
	for rows.Next() {
		var l lineage
		if e = rows.Scan(&l.pvc, &l.st, &l.name, &l.pod); e != nil {
			rows.Close()
			return nil, e
		}
		lineages = append(lineages, l)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	for _, l := range lineages {
		if stByUID[l.st] != nil {
			podOwner[l.pod] = l.st
		}
	}
	byName := map[string]map[string]any{}
	for _, pvc := range pvcs {
		meta := objectMap(pvc, "metadata")
		name, uid := objectString(meta, "name"), objectString(meta, "uid")
		byName[name] = pvc
		owner := claimOwner[name]
		if owner == "" {
			for _, raw := range anySlice(meta["ownerReferences"]) {
				ref, _ := raw.(map[string]any)
				uid := objectString(ref, "uid")
				if stByUID[uid] != nil {
					owner = uid
					break
				}
				if st := podOwner[uid]; st != "" {
					owner = st
					break
				}
			}
		}
		if owner == "" && trusted[name][uid] {
			for st := range affected {
				if name == agentClaimName(i.Name, shards[st]) {
					owner = st
					break
				}
			}
		}
		if owner == "" {
			for st := range affected {
				if name == agentClaimName(i.Name, shards[st]) {
					return nil, Error("CONFLICT", "受影响确定名称PVC缺少原UID父链，不能忽略或认领")
				}
			}
		}
		if owner == "" {
			for _, l := range lineages {
				if l.pvc == uid && affected[l.st] != "" {
					owner = l.st
					break
				}
			}
		}
		policy := affected[owner]
		if policy == "" {
			continue
		}
		for _, raw := range anySlice(meta["ownerReferences"]) {
			ref, _ := raw.(map[string]any)
			rUID := objectString(ref, "uid")
			if stByUID[rUID] == nil && podOwner[rUID] == "" {
				return nil, Error("CONFLICT", "PVC存在非原owner UID引用，不能确认存储影响")
			}
			if policy == "Retain" && (rUID == owner || podOwner[rUID] == owner) {
				return nil, Error("CONFLICT", "Retain尚有待删ST/Pod owner引用，请等待Kubernetes完成采用")
			}
		}
		if uid != "" && !trusted[name][uid] && stByUID[owner] != nil {
			if err := s.rememberAgentVolumes(ctx, i, []agentVolumeLink{{pvcUID: uid, name: name, stUID: owner, stName: objectString(objectMap(stByUID[owner], "metadata"), "name")}}); err != nil {
				return nil, err
			}
			if trusted[name] == nil {
				trusted[name] = map[string]bool{}
			}
			trusted[name][uid] = true
		}
		if uid == "" || !trusted[name][uid] {
			return nil, Error("CONFLICT", "受影响PVC缺少原UID关系证明")
		}
		st := stByUID[owner]
		out.volumes = append(out.volumes, agentAffectedVolume{Namespace: i.TargetNamespace, Name: name, UID: uid, Policy: policy, StatefulUID: owner, StatefulName: objectString(objectMap(st, "metadata"), "name"), Shard: shards[owner]})
	}
	// 关键：恢复/新增shard可复用本实例原UID历史Retain卷，但不能接管同名新UID或孤卷。
	if !deleting && nextReplicas > 0 {
		for shard := int32(0); shard < nextShards; shard++ {
			if oldReplicas > 0 && shard < oldShards {
				continue
			}
			if e = ctx.Err(); e != nil {
				return nil, e
			}
			name := agentClaimName(i.Name, shard)
			var pvc map[string]any
			e = t.Request(ctx, "GET", "/api/v1/namespaces/"+ns+"/persistentvolumeclaims/"+url.PathEscape(name), nil, &pvc)
			if apiCode(e) == "TARGET_NOT_FOUND" {
				continue
			}
			if e != nil {
				return nil, e
			}
			uid := objectString(objectMap(pvc, "metadata"), "uid")

			if uid == "" || !trusted[name][uid] {
				return nil, Error("CONFLICT", "恢复或新增shard的确定WAL名称已被非原PVC占用，请使用新实例名称")
			}
			if objectString(objectMap(pvc, "metadata"), "deletionTimestamp") != "" {
				return nil, Error("CONFLICT", "原PVC删除中，不能恢复挂载")
			}
		}
	}
	sort.Slice(out.facts, func(a, b int) bool { return out.facts[a].UID < out.facts[b].UID })
	sort.Slice(out.policies, func(a, b int) bool { return out.policies[a].UID < out.policies[b].UID })
	sort.Slice(out.volumes, func(a, b int) bool { return out.volumes[a].UID < out.volumes[b].UID })
	return out, nil
}

// agentStorageImpact只绑定会改变当次危险操作影响的规范事实，Pod滚动和status/RV不参与指纹。
func (s *Store) agentStorageImpact(i *rpc.ObservationInstance, snapshot *agentStorageSnapshot) string {
	if snapshot == nil || !snapshot.dangerous || snapshot.mode != "PVC" {
		return ""
	}
	return s.fingerprint(i, "agent-storage-impact/v1:"+canonical(struct {
		Mode     string
		Policies []agentPolicyImpact
		Volumes  []agentAffectedVolume
	}{snapshot.mode, snapshot.facts, snapshot.volumes}))
}
func (s *Store) validateAgentStorageImpact(i *rpc.ObservationInstance, baseline string, snapshot *agentStorageSnapshot) error {
	if baseline != s.agentStorageImpact(i, snapshot) {
		return Error("CONFLICT", "实际存储影响已变化，请重新预览确认")
	}
	return nil
}
