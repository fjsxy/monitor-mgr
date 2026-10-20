// Kitex默认只限制客户端等待；显式传递剩余预算并限制服务端context，防下游在超时后继续占用。
package platform

import (
	"context"
	"strconv"
	"time"

	"github.com/cloudwego/kitex/pkg/remote"
	headermeta "github.com/cloudwego/kitex/pkg/remote/transmeta"
	"github.com/cloudwego/kitex/pkg/transmeta"
)

type rpcBudgetMeta struct{ remote.MetaHandler }

// WriteMeta 在TTHeader已有身份/方法元信息上附加实际剩余时间，不传递浏览器可伪造的角色信息。
func (h rpcBudgetMeta) WriteMeta(ctx context.Context, msg remote.Message) (context.Context, error) {
	ctx, e := h.MetaHandler.WriteMeta(ctx, msg)
	if e != nil {
		return ctx, e
	}
	budget := 10 * time.Second
	if deadline, ok := ctx.Deadline(); ok {
		if remaining := time.Until(deadline); remaining < budget {
			budget = remaining
		}
	}
	// 关键：毫秒向下取整；不足1毫秒时拒绝新派发，不能把0误读成无限预算。
	ms := budget.Milliseconds()
	if ms < 1 {
		return ctx, context.DeadlineExceeded
	}
	msg.TransInfo().PutTransIntInfo(map[uint16]string{headermeta.RPCTimeout: strconv.FormatInt(ms, 10)})
	return ctx, nil
}

var budgetClientMeta remote.MetaHandler = rpcBudgetMeta{MetaHandler: transmeta.ClientTTHeaderHandler}
