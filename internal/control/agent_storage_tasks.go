// 存储核验与实例生命周期分离：只读原PVC UID，实例完成后仍保存未决管理责任。
package control

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/url"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// storageTasksSchema与首次存储v10一同引入；每次派发保留独立不可替换的卷身份列表。
var storageTasksSchema = []string{
	`ALTER TABLE publication_previews ADD COLUMN storage_fingerprint TEXT NOT NULL DEFAULT ''`,
	`CREATE TABLE agent_storage_tasks(attempt_id TEXT PRIMARY KEY REFERENCES execution_attempts(id),operation_id TEXT NOT NULL REFERENCES operations(id),instance_id TEXT NOT NULL REFERENCES instances(id),agent_uid TEXT NOT NULL,mode TEXT NOT NULL,state TEXT NOT NULL,volumes BLOB NOT NULL,last_attempt_at TEXT NOT NULL)`,
	`CREATE INDEX pending_agent_storage_tasks ON agent_storage_tasks(state,operation_id)`,
}

func snapshotVolumes(snapshot *agentStorageSnapshot, agentUID string) []*rpc.VolumeObservation {
	out := []*rpc.VolumeObservation{}
	if snapshot == nil {
		return out
	}
	for _, v := range snapshot.volumes {
		out = append(out, &rpc.VolumeObservation{Namespace: v.Namespace, Name: v.Name, Uid: v.UID, AccessModes: []string{}, MountedByPods: []string{}, ExpectedPolicy: reference(v.Policy), ImpactAction: reference(v.Action), RetentionResult_: reference("Pending"), StatefulUID: reference(v.StatefulUID), StatefulName: reference(v.StatefulName), Shard: reference(v.Shard), AgentUID: reference(agentUID)})
	}
	return out
}
func snapshotPolicies(snapshot *agentStorageSnapshot) []*rpc.AgentStatefulPolicy {
	out := []*rpc.AgentStatefulPolicy{}
	if snapshot == nil {
		return out
	}
	for _, p := range snapshot.policies {
		out = append(out, &rpc.AgentStatefulPolicy{Name: p.Name, Uid: p.UID, WhenScaled: p.WhenScaled, WhenDeleted: p.WhenDeleted})
	}
	return out
}

// activateStorageTask必须在possibly_sent事务内；未发/取消不会留下虚构的卷删除责任。
func (s *Store) activateStorageTask(ctx context.Context, tx *sql.Tx, o *rpc.Operation, snapshot *agentStorageSnapshot) error {
	if snapshot == nil {
		return nil
	}
	var uid string
	if e := tx.QueryRowContext(ctx, `SELECT uid FROM instances WHERE id=?`, o.GetInstanceId()).Scan(&uid); e != nil {
		return e
	}
	raw, e := json.Marshal(snapshotVolumes(snapshot, uid))
	if e != nil {
		return e
	}
	state := "PENDING"
	if len(snapshot.volumes) == 0 {
		state = "DONE"
	}
	_, e = tx.ExecContext(ctx, `INSERT INTO agent_storage_tasks VALUES(?,?,?,?,?,?,?,?)`, o.GetAttemptId(), o.OperationId, o.GetInstanceId(), uid, snapshot.mode, state, raw, "")
	return e
}

type storageRowsQuery interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}

// enrichStorageTask仅读取持久核验结果；不能用CR写入/删除成功替代PVC结果。
func (s *Store) enrichStorageTask(ctx context.Context, q clusterQuery, o *rpc.Operation) {
	reader, ok := q.(storageRowsQuery)
	if !ok {
		return
	}
	rows, e := reader.QueryContext(ctx, `SELECT t.mode,t.state,t.volumes FROM agent_storage_tasks t JOIN execution_attempts a ON a.id=t.attempt_id WHERE t.operation_id=? ORDER BY a.sequence`, o.OperationId)
	if e != nil {
		return
	}
	volumes := []*rpc.VolumeObservation{}
	found := false
	mode := ""
	allCancelled := true
	for rows.Next() {
		var state string
		var raw []byte
		if rows.Scan(&mode, &state, &raw) != nil {
			continue
		}
		found = true
		if state != "CANCELLED" {
			allCancelled = false
			var vs []*rpc.VolumeObservation
			if json.Unmarshal(raw, &vs) == nil {
				volumes = append(volumes, vs...)
			}
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return
	}
	if !found {
		return
	}
	result := "NOT_AFFECTED"
	if mode == "emptyDir" {
		result = "NOT_APPLICABLE"
	} else if allCancelled {
		result = "NotApplied"
	} else if len(volumes) > 0 {
		result = volumes[0].GetRetentionResult_()
		for _, v := range volumes {
			r := v.GetRetentionResult_()
			if r == "Unknown" {
				result = "Unknown"
				break
			}
			if r == "Pending" || r == "Deleting" {
				result = "Deleting"
			} else if result != r && result != "Deleting" {
				result = "Completed"
			}
		}
	}
	o.StorageResult_ = &result
	o.StorageVolumes = volumes
}

// verifyAgentStorageTasks每次只读指定操作的原UID卷；使用当前ACTIVE凭据，允许轮换凭据后继续核验。
func (s *Store) verifyAgentStorageTasks(ctx context.Context, operation string) {
	key := s.observationEpoch + "storage:" + operation
	if _, busy := recoveryFlights.LoadOrStore(key, true); busy {
		return
	}
	defer recoveryFlights.Delete(key)
	s.verifyAgentStorageTasksLocked(ctx, operation)
}

// verifyAgentStorageTasksLocked由同操作只读flight串行执行，旧读取不能覆盖更新的卷结果。
func (s *Store) verifyAgentStorageTasksLocked(ctx context.Context, operation string) {
	rows, e := s.DB.QueryContext(ctx, `SELECT t.attempt_id,t.instance_id,t.agent_uid,t.volumes,o.cluster_id,o.state FROM agent_storage_tasks t JOIN operations o ON o.id=t.operation_id WHERE t.operation_id=? AND t.state='PENDING'`, operation)
	if e != nil {
		return
	}
	type task struct {
		attempt, instance, uid, cluster, state string
		raw                                    []byte
	}
	tasks := []task{}
	for rows.Next() {
		var v task
		if rows.Scan(&v.attempt, &v.instance, &v.uid, &v.raw, &v.cluster, &v.state) == nil {
			tasks = append(tasks, v)
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return
	}
	for _, v := range tasks {
		if v.state == "FAILED" || v.state == "CANCELLED" {
			s.DB.ExecContext(ctx, `UPDATE agent_storage_tasks SET state='CANCELLED' WHERE attempt_id=? AND state='PENDING'`, v.attempt)
			continue
		}
		if v.state != "SUCCEEDED" && v.state != "DELETION_VERIFYING" && v.state != "DELETION_BLOCKED" && v.state != "RESULT_UNCERTAIN" {
			continue
		}
		var volumes []*rpc.VolumeObservation
		if json.Unmarshal(v.raw, &volumes) != nil {
			continue
		}
		target, currentCluster, targetErr := s.targetForRecovery(ctx, v.cluster)
		now := s.Now().UTC().Format(time.RFC3339Nano)
		done := true
		for _, volume := range volumes {
			// 关键：已确认Retain/Deleted是该次操作的终态，不延迟实例完成，也不继续占用凭据责任。
			if volume.GetRetentionResult_() == "Retained" || volume.GetRetentionResult_() == "Deleted" || volume.GetRetentionResult_() == "Lost" {
				continue
			}
			volume.LastAttemptAt = &now
			cause := targetErr
			var pvc map[string]any
			if cause == nil {
				cause = target.Request(ctx, "GET", "/api/v1/namespaces/"+url.PathEscape(volume.Namespace)+"/persistentvolumeclaims/"+url.PathEscape(volume.Name), nil, &pvc)
			}
			result := "Unknown"
			message := ""
			if apiCode(cause) == "TARGET_NOT_FOUND" {
				if volume.GetExpectedPolicy() == "Delete" {
					result = "Deleted"
				} else {
					result = "Lost"
					message = "原Retain PVC已缺失，不代表WAL保留"
				}
				volume.LastSuccessAt = &now
			} else if cause != nil {
				message = safeSourceError(cause)
			} else if objectString(objectMap(pvc, "metadata"), "uid") != volume.Uid {
				message = "同名PVC出现新UID，禁止认领或清理"
			} else {
				volume.Phase = objectString(objectMap(pvc, "status"), "phase")
				volume.LastSuccessAt = &now
				if volume.GetExpectedPolicy() == "Delete" {
					result = "Deleting"
				} else if objectString(objectMap(pvc, "metadata"), "deletionTimestamp") != "" {
					message = "原Retain PVC删除中，结果无法确认"
				} else if ownerErr := target.verifyRetainedAgentOwner(ctx, volume, pvc); ownerErr != nil {
					message = safeSourceError(ownerErr)
				} else if v.state == "SUCCEEDED" {
					result = "Retained"
				} else {
					result = "Pending"
				}
			}
			volume.RetentionResult_ = &result
			volume.Error = &message
			if result == "Unknown" || result == "Deleting" || result == "Pending" {
				done = false
			}
		}
		if target != nil {
			target.Close()
		}
		raw, _ := json.Marshal(volumes)
		state := "PENDING"
		if done {
			state = "DONE"
		}
		tx, err := s.DB.BeginTx(ctx, nil)
		if err != nil {
			continue
		}
		valid := true
		if targetErr == nil {
			var n int
			valid = tx.QueryRowContext(ctx, `SELECT count(*) FROM clusters c JOIN instances i ON i.cluster_id=c.id WHERE c.id=? AND c.uid=? AND c.state='ACTIVE' AND c.credential_revision=? AND i.id=? AND i.uid=?`, v.cluster, currentCluster.Uid, currentCluster.CredentialRevision, v.instance, v.uid).Scan(&n) == nil && n == 1
		}
		// 关键：迟到的旧凭据结果不能结束当前存储责任；已删除实例仍允许原UID只读核验。
		if valid {
			if _, err = tx.ExecContext(ctx, `UPDATE agent_storage_tasks SET state=?,volumes=?,last_attempt_at=? WHERE attempt_id=? AND state='PENDING'`, state, raw, now, v.attempt); err == nil {
				err = tx.Commit()
			}
		}
		tx.Rollback()
	}
}

// launchStorageRecovery沿共用应用生命周期启动只读工作，不另造计时器或重放队列。
func (s *Store) launchStorageRecovery(ctx context.Context) {
	rows, e := s.DB.QueryContext(ctx, `SELECT DISTINCT operation_id FROM agent_storage_tasks WHERE state='PENDING'`)
	if e != nil {
		return
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if rows.Scan(&id) == nil {
			ids = append(ids, id)
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return
	}
	for _, id := range ids {
		key := s.observationEpoch + "storage:" + id
		if _, busy := recoveryFlights.LoadOrStore(key, true); busy {
			continue
		}
		s.publicationWG.Add(1)
		go func(id, key string) {
			defer s.publicationWG.Done()
			defer recoveryFlights.Delete(key)
			s.verifyAgentStorageTasksLocked(ctx, id)
		}(id, key)
	}
}

// enrichAgentVolumeResults把独立任务结果按原PVC UID接入来源，不改写当前phase/挂载或给结果续期。
func (s *Store) enrichAgentVolumeResults(ctx context.Context, i *rpc.ObservationInstance, volumes []*rpc.VolumeObservation) error {
	if i.ReadOnly {
		return nil
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT volumes FROM agent_storage_tasks WHERE instance_id=? AND agent_uid=? AND state<>'CANCELLED' ORDER BY rowid`, i.Id, i.Uid)
	if e != nil {
		return e
	}
	defer rows.Close()
	latest := map[string]*rpc.VolumeObservation{}
	for rows.Next() {
		var raw []byte
		if e = rows.Scan(&raw); e != nil {
			return e
		}
		var results []*rpc.VolumeObservation
		if json.Unmarshal(raw, &results) != nil {
			return Error("UNAVAILABLE", "存储结果历史不可读")
		}
		for _, v := range results {
			latest[v.Uid] = v
		}
	}
	if e = rows.Err(); e != nil {
		return e
	}
	for _, v := range volumes {
		r := latest[v.Uid]
		if r == nil {
			continue
		}
		v.ExpectedPolicy = r.ExpectedPolicy
		v.RetentionResult_ = r.RetentionResult_
		v.LastAttemptAt = r.LastAttemptAt
		v.LastSuccessAt = r.LastSuccessAt
		v.Error = r.Error
		v.StatefulUID = r.StatefulUID
		v.StatefulName = r.StatefulName
		v.Shard = r.Shard
		v.AgentUID = r.AgentUID
		v.ImpactAction = r.ImpactAction
	}
	return nil
}

// verifyRetainedAgentOwner区分本次保留ST的缩零和会删除ST的操作，不能混用两个策略。
func (t *Target) verifyRetainedAgentOwner(ctx context.Context, v *rpc.VolumeObservation, pvc map[string]any) error {
	refs := anySlice(objectMap(pvc, "metadata")["ownerReferences"])
	if len(refs) == 0 {
		return nil
	}
	if v.GetImpactAction() != "whenScaled" {
		return Error("CONFLICT", "Retain PVC仍有待删除owner引用")
	}
	for _, raw := range refs {
		ref, _ := raw.(map[string]any)
		if objectString(ref, "kind") != "StatefulSet" || objectString(ref, "uid") != v.GetStatefulUID() {
			return Error("CONFLICT", "缩零Retain PVC仍有非保留ST的owner引用")
		}
	}
	var st map[string]any
	if e := t.Request(ctx, "GET", "/apis/apps/v1/namespaces/"+url.PathEscape(v.Namespace)+"/statefulsets/"+url.PathEscape(v.GetStatefulName()), nil, &st); e != nil {
		return e
	}
	meta := objectMap(st, "metadata")
	if objectString(meta, "uid") != v.GetStatefulUID() || objectString(meta, "deletionTimestamp") != "" {
		return Error("CONFLICT", "原保留ST身份已变化或正在删除")
	}
	scaled, _, e := agentRetention(objectMap(st, "spec"))
	if e != nil {
		return e
	}
	if scaled != "Retain" {
		return Error("CONFLICT", "原ST实际whenScaled不再为Retain")
	}
	return nil
}
