// 运行摘要仅由身份有效且适用于当前generation的CR状态推导，与发布写入结果分别表达。
package control

import (
	"context"
	"encoding/json"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"time"
)

func number(m map[string]any, key string) (int32, bool) {
	v, ok := m[key].(float64)
	if !ok || v < 0 || v > 2147483647 || v != float64(int32(v)) {
		return 0, false
	}
	return int32(v), true
}
func interpretKSM(cr map[string]any, now string) *rpc.InstanceRuntime {
	out := &rpc.InstanceRuntime{Summary: "Unknown", ObservedAt: now, LastSuccessAt: now}
	spec, status, meta := objectMap(cr, "spec"), objectMap(cr, "status"), objectMap(cr, "metadata")
	out.DesiredState = "Running"
	if spec["enabled"] == false {
		out.DesiredState = "Disabled"
	} else if spec["suspended"] == true {
		out.DesiredState = "Suspended"
	}
	gen, gok := meta["generation"].(float64)
	observed, ook := status["observedGeneration"].(float64)
	if !gok || !ook || gen != observed || len(status) == 0 {
		out.Error = "缺少适用的运行状态，当前未知"
		return out
	}
	phase := objectString(status, "phase")
	out.DeploymentMode = objectString(status, "deploymentMode")
	ready, reason := "", ""
	if conditions, ok := status["conditions"].([]any); ok {
		for _, v := range conditions {
			c, ok := v.(map[string]any)
			if !ok {
				continue
			}
			if objectString(c, "type") == "Ready" && c["observedGeneration"] == gen {
				ready = objectString(c, "status")
				reason = objectString(c, "reason")
			}
		}
	}
	if phase == "" || ready == "" {
		out.Error = "运行状态不完整，当前未知"
		return out
	}
	available, _ := number(status, "availablePods")
	desired, hasDesired := number(status, "desiredPods")
	switch {
	case out.DesiredState == "Suspended" && reason == "Suspended" && ready == "True" && phase == "Ready":
		out.Summary = "Suspended"
		available = 0
		desired = 0
	case out.DesiredState == "Disabled" && reason == "Disabled" && phase == "Disabled":
		out.Summary = "Disabled"
		available = 0
		desired = 0
	case out.DesiredState == "Running" && reason == "NoNamespaceMatched" && phase == "Ready" && ready == "True":
		out.Summary = "NoMatch"
		available = 0
		desired = 0
	case hasDesired && desired > 0 && out.DesiredState == "Running":
		out.Summary = "NotReady"
		if phase == "Ready" && ready == "True" && available >= desired {
			out.Summary = "Available"
		}
		if out.Summary != "Available" {
			out.Error = reason
		}
	default:
		out.Error = "运行意图尚未获得有效状态确认"
		return out
	}
	out.AvailablePods = &available
	out.DesiredPods = &desired
	return out
}

type savedObservation struct {
	Epoch   string               `json:"epoch"`
	Runtime *rpc.InstanceRuntime `json:"runtime"`
}

func (s *Store) refreshObserved(ctx context.Context, id string, t *Target) (*rpc.InstanceRuntime, error) {
	i, e := instanceByID(ctx, s.DB, id)
	if e != nil {
		return nil, e
	}
	if i.Lifecycle == "DELETED" {
		return &rpc.InstanceRuntime{Summary: "Deleted", ObservedAt: i.ActivityAt, LastSuccessAt: i.ActivityAt}, nil
	}
	ctx = context.WithValue(ctx, observationScopeKey{}, observationScope{i.Id, i.Uid, i.ClusterId, t.clusterUID, i.Name, i.TargetNamespace, t.credentialRevision})
	cr, e := t.readComponent(ctx, i)
	now := s.Now().UTC().Format(time.RFC3339Nano)
	projected := safeTargetDocument(cr)
	var out *rpc.InstanceRuntime
	if e != nil {
		out = &rpc.InstanceRuntime{Summary: "Unknown", ObservedAt: now, Error: "CR 读取失败，当前未知"}
	} else if cr == nil {
		out = &rpc.InstanceRuntime{Summary: "Missing", ObservedAt: now, LastSuccessAt: now, Error: "原 CR 当前不存在，不自动重建"}
	} else if i.Uid == "" || objectString(objectMap(cr, "metadata"), "uid") != i.Uid {
		out = &rpc.InstanceRuntime{Summary: "Unknown", ObservedAt: now, Error: "资源身份无法确认"}
	} else {
		// 关键：Agent/KSM摘要只从同一安全副本派生，status.reason不能越过YAML投影进入运行缓存。
		out = componentFor(i.Component).interpret(projected, now)
	}
	cause := e
	if cause == nil && cr != nil && (i.Uid == "" || objectString(objectMap(cr, "metadata"), "uid") != i.Uid) {
		cause = Error("CONFLICT", "资源身份无法确认")
	}
	s.saveSourceObservation(ctx, i, "CR", &rpc.SourceObservation{Source: "CR", RuntimeSnapshot: out}, cause)
	raw, _ := json.Marshal(savedObservation{Epoch: s.observationEpoch, Runtime: out})
	obj, _ := json.Marshal(projected)
	if cause == nil {
		tx, err := s.DB.BeginTx(ctx, nil)
		if err != nil {
			return nil, Error("UNAVAILABLE", "观测保存失败")
		}
		if !observationScopeCurrent(ctx, tx, ctx) {
			tx.Rollback()
			return s.GetRuntimeForObserved(ctx, i), nil
		}
		if _, saveErr := tx.ExecContext(ctx, `INSERT INTO instance_observations VALUES(?,?,?,?) ON CONFLICT(instance_id) DO UPDATE SET runtime=excluded.runtime,cr=excluded.cr,observed_at=excluded.observed_at`, id, raw, obj, now); saveErr != nil {
			tx.Rollback()
			return nil, Error("UNAVAILABLE", "观测保存失败")
		}
		if tx.Commit() != nil {
			return nil, Error("UNAVAILABLE", "观测保存失败")
		}
	}
	if e == nil && cr != nil && i.Uid != "" && objectString(objectMap(cr, "metadata"), "uid") == i.Uid {
		records, err := s.observeOwnerGraphRecords(ctx, i, t, i.TargetNamespace)
		matches := []*rpc.MatchedResource{}
		for _, record := range records {
			if record.kind == "Pod" {
				matches = append(matches, &rpc.MatchedResource{Kind: "Pod", Namespace: record.namespace, Name: record.name, Uid: record.uid})
			}
		}
		s.saveSourceObservation(ctx, i, "Pod", &rpc.SourceObservation{Source: "Pod", Matches: matches}, err)
	}
	if i.Component == AgentComponent && e == nil && cr != nil && i.Uid != "" && objectString(objectMap(cr, "metadata"), "uid") == i.Uid {
		s.refreshAgentAuxiliary(ctx, i, t, cr)
	}
	return runtimeFromSource(s.instanceSources(ctx, i), out), nil
}
func (s *Store) GetRuntime(ctx context.Context, c *rpc.RequestContext, r *rpc.InstanceID) (*rpc.InstanceRuntime, error) {
	i, e := s.authorizedInstance(ctx, c, r.Id)
	if e != nil {
		return nil, e
	}
	if i.ReadOnly {
		external, err := s.externalByID(ctx, i.Id)
		if err != nil {
			return nil, err
		}
		return external.Runtime, nil
	}
	if i.Lifecycle == "DELETED" {
		return &rpc.InstanceRuntime{Summary: "Deleted", ObservedAt: i.ActivityAt, LastSuccessAt: i.ActivityAt}, nil
	}
	if out := runtimeFromSource(s.instanceSources(ctx, i), nil); out != nil {
		return out, nil
	}
	var raw []byte
	if s.DB.QueryRowContext(ctx, `SELECT runtime FROM instance_observations WHERE instance_id=?`, i.Id).Scan(&raw) != nil {
		return &rpc.InstanceRuntime{Summary: "Unknown", Error: "尚未读取当前运行状态"}, nil
	}
	var saved savedObservation
	if json.Unmarshal(raw, &saved) != nil || saved.Runtime == nil {
		return nil, Error("UNAVAILABLE", "观测记录不可读")
	}
	out := saved.Runtime
	stamp, _ := time.Parse(time.RFC3339Nano, out.ObservedAt)
	if saved.Epoch != s.observationEpoch || s.Now().Sub(stamp) > 30*time.Second {
		out.Summary = "Unknown"
		out.Error = "历史观测，当前尚未重新读取或已过期"
		out.AvailablePods = nil
		out.DesiredPods = nil
		out.DeploymentMode = ""
	}
	return out, nil
}
func (s *Store) RefreshInstance(ctx context.Context, c *rpc.RequestContext, r *rpc.InstanceID) (*rpc.InstanceRuntime, error) {
	i, e := s.authorizedInstance(ctx, c, r.Id)
	if e != nil {
		return nil, e
	}
	if i.ReadOnly {
		if _, e = s.ListInstances(ctx, c, &rpc.ClusterID{Id: i.ClusterId}); e != nil {
			return nil, e
		}
		current, e := s.authorizedInstance(ctx, c, r.Id)
		if e != nil {
			return nil, e
		}
		return current.Runtime, nil
	}
	t, e := s.TargetForCluster(ctx, c, i.ClusterId)
	if e != nil {
		return nil, e
	}
	defer t.Close()
	return s.refreshObserved(ctx, i.Id, t)
}

// GetRuntimeForObserved 仅投影已经提交的同UID来源，迟到结果不回显为当前状态。
func (s *Store) GetRuntimeForObserved(ctx context.Context, i *rpc.ObservationInstance) *rpc.InstanceRuntime {
	return runtimeFromSource(s.instanceSources(ctx, i), &rpc.InstanceRuntime{Summary: "Unknown", Error: "观察目标修订已变化"})
}
