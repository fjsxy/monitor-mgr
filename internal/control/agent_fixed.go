// Agent固定字段以已确认/可能已发的不可变版本为基线，不能只依赖可变草稿保存时检查。
package control

import (
	"context"
	"database/sql"
	"encoding/json"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"k8s.io/apimachinery/pkg/api/resource"
)

// validateFixedSpec是保存、预览、受理、派发的共同约束；后续存储模式继续复用此入口。
func validateFixedSpec(ctx context.Context, q interface {
	QueryContext(context.Context, string, ...any) (*sql.Rows, error)
}, i *rpc.ObservationInstance, desired map[string]any) error {
	if i.Component != AgentComponent {
		return nil
	}
	rows, e := q.QueryContext(ctx, `SELECT effective_spec FROM publication_versions WHERE instance_id=? AND (id=? OR id IN (SELECT p.version_id FROM publication_operations p JOIN operations o ON o.id=p.operation_id WHERE p.instance_id=? AND o.state IN ('DISPATCHED','RESULT_UNCERTAIN','IDENTITY_UNCERTAIN','SUCCEEDED') AND EXISTS(SELECT 1 FROM execution_attempts a WHERE a.operation_id=o.id AND a.possibly_sent=1)))`, i.Id, i.CurrentVersionId, i.Id)
	if e != nil {
		return Error("UNAVAILABLE", "固定字段基线不可读")
	}
	defer rows.Close()
	for rows.Next() {
		var raw []byte
		if rows.Scan(&raw) != nil {
			return Error("UNAVAILABLE", "固定字段基线不可读")
		}
		var baseline map[string]any
		if json.Unmarshal(raw, &baseline) != nil {
			return Error("UNAVAILABLE", "固定字段基线损坏")
		}
		mode, capacity := agentStorageIdentity(baseline)
		nextMode, nextCapacity := agentStorageIdentity(desired)
		if mode == "" || nextMode == "" {
			return Error("CONFLICT", "存储身份无法确认，请新建实例")
		}
		equal := mode == nextMode
		if mode == "PVC" && nextMode == "PVC" {
			old, err := resource.ParseQuantity(capacity)
			next, nextErr := resource.ParseQuantity(nextCapacity)
			equal = err == nil && nextErr == nil && old.Cmp(next) == 0
		}
		if !equal {
			return Error("CONFLICT", "已创建或可能已发的存储模式与PVC容量固定，请新建实例")
		}
	}
	if rows.Err() != nil {
		return Error("UNAVAILABLE", "固定字段基线读取失败")
	}
	if rows.Close() != nil {
		return Error("UNAVAILABLE", "固定字段基线游标关闭失败")
	}
	return nil
}
func agentStorageIdentity(spec map[string]any) (string, string) {
	storage := objectMap(spec, "storage")
	if template, ok := storage["volumeClaimTemplate"].(map[string]any); ok {
		return "PVC", objectString(objectMap(objectMap(objectMap(template, "spec"), "resources"), "requests"), "storage")
	}
	if _, ok := storage["emptyDir"]; ok {
		return "emptyDir", ""
	}
	return "", ""
}
