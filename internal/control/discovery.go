// 外部发现仅保存只读投影；完整分页才替换本组件集合，失败保留历史并立即未知。
package control

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"sort"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// externalDocument 的epoch只留在持久读模型中，不是目标对象或所有权依据。
type externalDocument struct {
	*rpc.ObservationInstance
	Epoch string `json:"observationEpoch"`
}

func (s *Store) externalInstance(raw []byte, active bool) (*rpc.ObservationInstance, error) {
	doc := externalDocument{ObservationInstance: new(rpc.ObservationInstance)}
	if json.Unmarshal(raw, &doc) != nil || !doc.ReadOnly {
		return nil, Error("UNAVAILABLE", "发现缓存不可读")
	}
	i := doc.ObservationInstance
	if len(i.Sources) == 0 && i.Runtime != nil {
		i.Sources = []*rpc.SourceObservation{{Source: "CR", Uid: reference(i.Uid), LastAttemptAt: i.Runtime.ObservedAt, LastSuccessAt: i.Runtime.LastSuccessAt, RuntimeSnapshot: i.Runtime}}
	}
	for _, source := range i.Sources {
		stamp, e := time.Parse(time.RFC3339Nano, source.LastSuccessAt)
		stale := e == nil && s.Now().Sub(stamp) > 30*time.Second
		source.Stale = reference(stale)
		source.Historical = source.Historical || !active || doc.Epoch != s.observationEpoch || stale || source.Error != ""
	}
	i.Runtime = runtimeFromSource(i.Sources, &rpc.InstanceRuntime{Summary: "Unknown", Error: "尚未成功读取"})
	return i, nil
}
func (s *Store) cachedExternal(ctx context.Context, cluster string) ([]*rpc.ObservationInstance, error) {
	var state string
	if s.DB.QueryRowContext(ctx, `SELECT state FROM clusters WHERE id=?`, cluster).Scan(&state) != nil {
		return nil, Error("UNAVAILABLE", "集群不可读")
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT document FROM discovered_instances WHERE cluster_id=?`, cluster)
	if e != nil {
		return nil, Error("UNAVAILABLE", "发现缓存不可读")
	}
	defer rows.Close()
	out := []*rpc.ObservationInstance{}
	for rows.Next() {
		var raw []byte
		if rows.Scan(&raw) != nil {
			return nil, Error("UNAVAILABLE", "发现缓存不可读")
		}
		i, e := s.externalInstance(raw, state == "ACTIVE")
		if e != nil {
			return nil, e
		}
		out = append(out, i)
	}
	if rows.Err() != nil {
		return nil, Error("UNAVAILABLE", "发现缓存不可读")
	}
	sort.Slice(out, func(a, b int) bool {
		if out[a].Name == out[b].Name {
			return out[a].Id < out[b].Id
		}
		return out[a].Name < out[b].Name
	})
	return out, nil
}

// discoverKSM 保留原RPC接缝并覆盖两组件，各自失败不覆盖另一组件的成功。
func (s *Store) discoverKSM(ctx context.Context, c *rpc.RequestContext, cluster string, managed []*rpc.ObservationInstance) ([]*rpc.ObservationInstance, error) {
	t, e := s.TargetForCluster(ctx, c, cluster)
	if e != nil {
		for _, component := range []string{KSMComponent, AgentComponent} {
			if err := s.persistDiscovery(ctx, cluster, component, nil, e, nil); err != nil {
				return nil, err
			}
		}
		return s.cachedExternal(ctx, cluster)
	}
	defer t.Close()
	if e = s.discoverTarget(ctx, cluster, managed, t); e != nil {
		return nil, e
	}
	return s.cachedExternal(ctx, cluster)
}

func (s *Store) discoverTarget(ctx context.Context, cluster string, managed []*rpc.ObservationInstance, t *Target) error {
	var result error
	byUID := map[string]bool{}
	for _, i := range managed {
		if i.Uid != "" {
			byUID[i.Uid] = true
		}
	}
	for _, component := range []string{KSMComponent, AgentComponent} {
		objects, e := t.listObjects(ctx, componentFor(component).collectionPath(""))
		out := []*rpc.ObservationInstance{}
		if e == nil {
			for _, cr := range objects {
				meta := objectMap(cr, "metadata")
				uid, name := objectString(meta, "uid"), objectString(meta, "name")
				if uid == "" || name == "" {
					e = Error("TARGET_ERROR", "发现对象身份不完整")
					break
				}
				if byUID[uid] {
					continue
				}
				ns, scope, crns, typ := objectString(objectMap(cr, "spec"), "targetNamespace"), "Cluster", "", "exporter"
				if component == AgentComponent {
					ns, scope, crns, typ = objectString(meta, "namespace"), "Namespaced", objectString(meta, "namespace"), "collector"
					if ns == "" {
						e = Error("TARGET_ERROR", "Agent namespace身份缺失")
						break
					}
				}
				projected := safeTargetDocument(cr)
				text := yamlText(projected)
				now := s.Now().UTC().Format(time.RFC3339Nano)
				runtime := componentFor(component).interpret(projected, now)
				h := sha256.Sum256([]byte(cluster + ":" + uid))
				id := "external-" + hex.EncodeToString(h[:])
				i := &rpc.ObservationInstance{Id: id, ClusterId: cluster, Component: component, Type: typ, ReadOnly: true, Ownership: "unmanaged", Name: name, TargetNamespace: ns, CrNamespace: &crns, Scope: &scope, Uid: uid, Lifecycle: "DISCOVERED", Runtime: runtime, Yaml: &text}
				i.Sources = []*rpc.SourceObservation{{Source: "CR", Uid: &uid, LastAttemptAt: now, LastSuccessAt: now, RuntimeSnapshot: runtime, Stale: reference(false)}}
				out = append(out, i)
			}
		}
		if err := s.persistDiscovery(ctx, cluster, component, out, e, t); err != nil {
			result = err
		}
	}
	return result
}

// persistDiscovery 与当前凭据比较后提交；成功全集才允许移除未返回对象。
func (s *Store) persistDiscovery(ctx context.Context, cluster, component string, current []*rpc.ObservationInstance, cause error, target *Target) error {
	durable, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Second)
	defer cancel()
	tx, e := s.DB.BeginTx(durable, nil)
	if e != nil {
		return Error("UNAVAILABLE", "发现记录保存失败")
	}
	defer tx.Rollback()
	if target != nil {
		var n int
		if tx.QueryRowContext(durable, `SELECT count(*) FROM clusters WHERE id=? AND state='ACTIVE' AND uid=? AND credential_revision=?`, cluster, target.clusterUID, target.credentialRevision).Scan(&n) != nil || n != 1 {
			return Error("CONFLICT", "发现目标修订已变化")
		}
	}
	if cause != nil {
		rows, e := tx.QueryContext(durable, `SELECT document FROM discovered_instances WHERE cluster_id=?`, cluster)
		if e != nil {
			return e
		}
		current = []*rpc.ObservationInstance{}
		for rows.Next() {
			var raw []byte
			if rows.Scan(&raw) != nil {
				rows.Close()
				return Error("UNAVAILABLE", "发现记录不可读")
			}
			doc := externalDocument{ObservationInstance: new(rpc.ObservationInstance)}
			if json.Unmarshal(raw, &doc) != nil {
				rows.Close()
				return Error("UNAVAILABLE", "发现记录不可读")
			}
			if doc.Component != component {
				continue
			}
			if len(doc.Sources) == 0 && doc.Runtime != nil {
				doc.Sources = []*rpc.SourceObservation{{Source: "CR", Uid: reference(doc.Uid), LastAttemptAt: doc.Runtime.ObservedAt, LastSuccessAt: doc.Runtime.LastSuccessAt, RuntimeSnapshot: doc.Runtime}}
			}
			for _, source := range doc.Sources {
				if source.Source == "CR" {
					source.LastAttemptAt = s.Now().UTC().Format(time.RFC3339Nano)
					source.Error = safeSourceError(cause)
					source.Historical = true
				}
			}
			doc.Runtime = runtimeFromSource(doc.Sources, &rpc.InstanceRuntime{Summary: "Unknown", Error: safeSourceError(cause)})
			current = append(current, doc.ObservationInstance)
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return e
		}
	} else {
		// 关键：分页失败不能执行DELETE；组件分区避免KSM成功删掉Agent失败时保留的历史。
		if _, e = tx.ExecContext(durable, `DELETE FROM discovered_instances WHERE cluster_id=? AND json_extract(document,'$.component')=?`, cluster, component); e != nil {
			return e
		}
	}
	for _, i := range current {
		raw, _ := json.Marshal(externalDocument{i, s.observationEpoch})
		if _, e = tx.ExecContext(durable, `INSERT INTO discovered_instances(id,cluster_id,document) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document`, i.Id, cluster, raw); e != nil {
			return e
		}
	}
	if tx.Commit() != nil {
		return Error("UNAVAILABLE", "发现记录保存失败")
	}
	return nil
}

// externalByID 仅供当前授权后的详情读取，仍按epoch/来源投影，不能直接返回旧Available。
func (s *Store) externalByID(ctx context.Context, id string) (*rpc.ObservationInstance, error) {
	var raw []byte
	var state string
	e := s.DB.QueryRowContext(ctx, `SELECT d.document,c.state FROM discovered_instances d JOIN clusters c ON c.id=d.cluster_id WHERE d.id=?`, id).Scan(&raw, &state)
	if e == sql.ErrNoRows {
		return nil, Error("NOT_FOUND", "实例不存在")
	}
	if e != nil {
		return nil, Error("UNAVAILABLE", "发现缓存不可读")
	}
	return s.externalInstance(raw, state == "ACTIVE")
}
