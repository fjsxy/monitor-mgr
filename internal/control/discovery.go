// 外部发现只保存可授权读取的只读文档，不能产生平台创建关系或生命周期所有权。
package control

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"sort"
)

func (s *Store) discoverKSM(ctx context.Context, c *rpc.RequestContext, cluster string, managed []*rpc.ObservationInstance) ([]*rpc.ObservationInstance, error) {
	t, e := s.TargetForCluster(ctx, c, cluster)
	if e != nil {
		return nil, e
	}
	defer t.Close()
	objects := []map[string]any{}
	for _, component := range []string{KSMComponent, AgentComponent} {
		items, e := t.listObjects(ctx, componentFor(component).collectionPath(""))
		if e != nil {
			return nil, e
		}
		objects = append(objects, items...)
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
		projected := safeTargetDocument(cr)
		text := yamlText(projected)
		component, typ, namespace, scope, crNamespace := KSMComponent, "exporter", objectString(objectMap(cr, "spec"), "targetNamespace"), "Cluster", ""
		if objectString(cr, "kind") == "PrometheusAgent" {
			component, typ, namespace, scope, crNamespace = AgentComponent, "collector", objectString(meta, "namespace"), "Namespaced", objectString(meta, "namespace")
			if namespace == "" {
				return nil, Error("TARGET_ERROR", "Agent namespace身份缺失")
			}
		}
		// 关键：运行错误也来自目标status，必须和YAML共用安全来源，不能从原reason旁路泄漏认证URL。
		i := &rpc.ObservationInstance{Id: id, ClusterId: cluster, Component: component, Type: typ, ReadOnly: true, Ownership: "unmanaged", Name: name, TargetNamespace: namespace, CrNamespace: &crNamespace, Scope: &scope, Uid: uid, Lifecycle: "DISCOVERED", Runtime: componentFor(component).interpret(projected, now), Yaml: &text}
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
