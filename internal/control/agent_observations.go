package control

import (
	"context"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// PreviewAssociations仅执行独立只读匹配；实现完成前明确拒绝，不把未知当零匹配。
func (s *Store) PreviewAssociations(ctx context.Context, c *rpc.RequestContext, r *rpc.AssociationPreviewRequest) (*rpc.AssociationPreview, error) {
	if _, e := s.authorizedInstance(ctx, c, r.Id); e != nil {
		return nil, e
	}
	return nil, Error("UNAVAILABLE", "采集关联读取尚不可用")
}
