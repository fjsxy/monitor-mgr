package control

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"net/url"
	"sort"
)

func (s *Store) discoverKSM(ctx context.Context, c *rpc.RequestContext, cluster string, managed []*rpc.ObservationInstance) ([]*rpc.ObservationInstance, error) {
	t, e := s.TargetForCluster(ctx, c, cluster)
	if e != nil {
		return nil, e
	}
	defer t.Close()
	objects := []map[string]any{}
	continuation := ""
	seen := map[string]bool{}
	for {
		var list struct {
			Metadata struct {
				Continue string `json:"continue"`
			} `json:"metadata"`
			Items []map[string]any `json:"items"`
		}
		if e = t.Request(ctx, "GET", ksmPath+"?limit=500&continue="+url.QueryEscape(continuation), nil, &list); e != nil {
			return nil, e
		}
		objects = append(objects, list.Items...)
		if list.Metadata.Continue == "" {
			break
		}
		if seen[list.Metadata.Continue] {
			return nil, Error("TARGET_ERROR", "KSM 分页未前进，发现结果不完整")
		}
		seen[list.Metadata.Continue] = true
		continuation = list.Metadata.Continue
	}
	byUID := map[string]bool{}
	for _, i := range managed {
		if i.Uid != "" {
			byUID[i.Uid] = true
		}
	}
	out := []*rpc.ObservationInstance{}
	now := s.Now().UTC().Format("2006-01-02T15:04:05.999999999Z07:00")
	for _, cr := range objects {
		meta := objectMap(cr, "metadata")
		uid, name := objectString(meta, "uid"), objectString(meta, "name")
		if uid == "" || name == "" {
			return nil, Error("TARGET_ERROR", "发现对象身份不完整")
		}
		if byUID[uid] {
			continue
		}
		h := sha256.Sum256([]byte(cluster + ":" + uid))
		id := "external-" + hex.EncodeToString(h[:])
		text := yamlText(cr)
		i := &rpc.ObservationInstance{Id: id, ClusterId: cluster, Component: "kube-state-metrics", Type: "exporter", ReadOnly: true, Ownership: "unmanaged", Name: name, TargetNamespace: objectString(objectMap(cr, "spec"), "targetNamespace"), Uid: uid, Lifecycle: "DISCOVERED", Runtime: interpretKSM(cr, now), Yaml: &text}
		out = append(out, i)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, e
	}
	defer tx.Rollback()
	if _, e = tx.ExecContext(ctx, `DELETE FROM discovered_instances WHERE cluster_id=?`, cluster); e != nil {
		return nil, e
	}
	for _, i := range out {
		raw, _ := json.Marshal(i)
		if _, e = tx.ExecContext(ctx, `INSERT INTO discovered_instances VALUES(?,?,?)`, i.Id, cluster, raw); e != nil {
			return nil, e
		}
	}
	if tx.Commit() != nil {
		return nil, Error("UNAVAILABLE", "发现记录保存失败")
	}
	return out, nil
}
