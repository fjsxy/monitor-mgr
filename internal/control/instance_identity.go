// 实例创建身份与当前管理权限分别表达，历史未知事实不能由账号当前角色补造。
package control

import (
	"context"
	"database/sql"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// instanceCreatorSchema 仅扩展字段；旧实例先保留未知，可靠历史补齐由后续流程负责。
var instanceCreatorSchema = []string{
	`ALTER TABLE instances ADD COLUMN creator_user_id TEXT NOT NULL DEFAULT ''`,
	`ALTER TABLE instances ADD COLUMN creator_role TEXT NOT NULL DEFAULT '' CHECK(creator_role IN ('','ADMIN','USER'))`,
}

// projectInstanceAccess 按当前身份投影管理能力，不改变平台纳管关系或固定创建身份。
func projectInstanceAccess(p *Principal, cl *rpc.Cluster, i *rpc.ObservationInstance) {
	i.CreatorIdentityKnown = i.CreatorUserId != "" && i.CreatorRole != ""
	i.ReadOnlyReason = ""
	switch {
	case i.ReadOnly:
		i.ReadOnlyReason = "平台未纳管实例只读"
	case cl.State != "ACTIVE":
		i.ReadOnlyReason = "集群已注销，历史只读"
	case i.Lifecycle == "DELETED":
		i.ReadOnlyReason = "实例已删除，历史只读"
	default:
		if e := instanceWritePermission(p, i); e != nil {
			i.ReadOnlyReason = e.(*rpc.APIError).Message
		}
	}
	i.CanManage = i.ReadOnlyReason == ""
}

// instanceWritePermission 在集群访问授权之上保护管理员实例，不限制普通用户间的创建关系。
func instanceWritePermission(p *Principal, i *rpc.ObservationInstance) error {
	// 关键：按创建时角色快照保护，角色升降和归属转移均不能改写创建事实。
	if p.Account.Role == "ADMIN" {
		return nil
	}
	if i.CreatorRole == "ADMIN" {
		return Error("FORBIDDEN", "管理员创建的实例对普通用户只读")
	}
	if i.CreatorRole != "USER" && !unknownRoleManagementAllowed(i) {
		return Error("FORBIDDEN", historicalReadOnlyReason(i))
	}
	return nil
}

// actorInstancePermission 用持久账号重验派发者，不能把受理时身份当作后续写入许可。
func actorInstancePermission(ctx context.Context, tx *sql.Tx, actor, id string) error {
	var a rpc.Account
	if e := tx.QueryRowContext(ctx, `SELECT id,role,enabled,must_change FROM accounts WHERE id=?`, actor).Scan(&a.Id, &a.Role, &a.Enabled, &a.MustChangePassword); e != nil || !a.Enabled || a.MustChangePassword {
		return Error("FORBIDDEN", "发起账号当前无操作权限")
	}
	i, e := instanceByID(ctx, tx, id)
	if e != nil {
		return e
	}
	return instanceWritePermission(&Principal{Account: &a}, i)
}

// authorizedInstanceForWrite 让预览在目标读取前复用命令授权，保持注销集群等原有拒绝语义。
func (s *Store) authorizedInstanceForWrite(ctx context.Context, c *rpc.RequestContext, id string) (*rpc.ObservationInstance, error) {
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	// 关键：预览只读取数据，但会形成后续写入确认；必须先执行同一实例写授权。
	_, i, e := s.instanceTx(ctx, tx, c, id)
	return i, e
}

// unknownRoleManagementAllowed 只对历史未知角色应用明确允许；已知管理员创建保护永远优先。
func unknownRoleManagementAllowed(i *rpc.ObservationInstance) bool {
	return i.CreatorRole == "" && i.ManagementConfirmation != nil && i.ManagementConfirmation.AllowClusterManagers
}

// historicalReadOnlyReason 区分等待确认与已明确保留管理员管理，避免把拒绝结果展示成未处理。
func historicalReadOnlyReason(i *rpc.ObservationInstance) string {
	if i.ManagementConfirmation != nil {
		return "管理员已确认此历史实例仅由管理员管理（创建时角色未知）"
	}
	return "创建时角色未知，须由管理员确认管理权限"
}
