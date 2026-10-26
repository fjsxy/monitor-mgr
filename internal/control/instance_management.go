// 管理员确认仅改变历史角色未知实例的管理权限，创建事实始终保持独立。
package control

import (
	"context"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// ConfirmInstanceManagement 与派发锁串行提交确认，撤销成功后尚未发送的请求不能沿用旧允许结果。
func (s *Store) ConfirmInstanceManagement(ctx context.Context, c *rpc.RequestContext, r *rpc.ConfirmInstanceManagementRequest) (*rpc.ObservationInstance, error) {
	s.dispatch.Lock()
	defer s.dispatch.Unlock()
	tx, e := s.DB.BeginTx(ctx, nil)
	if e != nil {
		return nil, Error("UNAVAILABLE", "暂不可用")
	}
	defer tx.Rollback()
	p, e := s.AuthorizeTx(ctx, tx, token(c))
	if e != nil {
		return nil, e
	}
	// 关键：RPC 自身重新认证管理员；HTTP按钮和幂等命中都不能代替当前授权。
	if e = requireAdmin(p); e != nil {
		return nil, e
	}
	_, i, e := s.instanceTx(ctx, tx, c, r.Id)
	if e != nil {
		return nil, e
	}
	if i.CreatorRole != "" {
		return nil, Error("CONFLICT", "只有创建时角色未知的历史实例可以确认管理权限")
	}
	if i.Lifecycle == "DELETED" {
		return nil, Error("CONFLICT", "实例已删除，历史只读")
	}
	var replay rpc.ObservationInstance
	cmd, hit, e := s.beginCommand(ctx, tx, p, c, "CONFIRM_INSTANCE_MANAGEMENT", i.Id, r, &replay)
	if e != nil {
		return nil, e
	}
	cl, e := clusterByID(ctx, tx, i.ClusterId)
	if e != nil {
		return nil, e
	}
	if hit {
		projectInstanceAccess(p, cl, i)
		return i, nil
	}
	now := s.Now().UTC().Format(time.RFC3339Nano)
	// 关键：只追加权限决定，不修改 creator_user_id/creator_role，也不把当前角色倒填到历史。
	if _, e = tx.ExecContext(ctx, `INSERT INTO instance_management_confirmations(instance_id,actor_id,confirmed_at,allow_cluster_managers) VALUES(?,?,?,?)`, i.Id, p.Account.Id, now, r.AllowClusterManagers); e != nil {
		return nil, Error("UNAVAILABLE", "确认记录保存失败")
	}
	if e = audit(ctx, tx, p.Account.Id, "CONFIRM_INSTANCE_MANAGEMENT", i.Id, s.Now()); e != nil {
		return nil, e
	}
	i.ManagementConfirmation = &rpc.InstanceManagementConfirmation{ActorId: p.Account.Id, ConfirmedAt: now, AllowClusterManagers: r.AllowClusterManagers}
	projectInstanceAccess(p, cl, i)
	if e = s.finishCommand(ctx, tx, cmd, i); e != nil {
		return nil, e
	}
	if e = tx.Commit(); e != nil {
		return nil, Error("UNAVAILABLE", "确认记录保存失败")
	}
	return i, nil
}
