// 工作台查询只消费授权范围内的读模型，在完整集合上统计、字面AND过滤及分页。
package control

import (
	"context"
	"strings"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// QueryInstances 保留旧ListInstances协议，新查询不因搜索/翻页向目标发起同步网络请求。
func (s *Store) QueryInstances(ctx context.Context, c *rpc.RequestContext, r *rpc.QueryInstancesRequest) (*rpc.QueryInstancesResult_, error) {
	if _, e := s.GetCluster(ctx, c, &rpc.ClusterID{Id: r.ClusterId}); e != nil {
		return nil, e
	}
	if e := rejectUnknownFields(c, "context"); e != nil {
		return nil, e
	}
	if e := rejectUnknownFields(r, "request"); e != nil {
		return nil, e
	}
	page, size := int32(1), int32(20)
	if r.Page != nil {
		page = *r.Page
	}
	if r.PageSize != nil {
		size = *r.PageSize
	}
	if page < 1 {
		return nil, fieldError("page", "页码必须至少为1")
	}
	if size < 1 || size > 100 {
		return nil, fieldError("pageSize", "每页数量必须为1–100")
	}
	if len(r.GetName()) > 253 {
		return nil, fieldError("name", "名称搜索不得超过253字节")
	}
	if v := r.GetType(); v != "" && v != "exporter" && v != "collector" {
		return nil, fieldError("type", "类型不支持")
	}
	if v := r.GetComponent(); v != "" && v != KSMComponent && v != AgentComponent {
		return nil, fieldError("component", "组件不支持")
	}
	if v := r.GetSummary(); v != "" {
		allowed := map[string]bool{"Unknown": true, "Available": true, "NotReady": true, "Suspended": true, "Disabled": true, "NoMatch": true, "Missing": true}
		if !allowed[v] {
			return nil, fieldError("summary", "运行状态不支持")
		}
	}
	rows, e := s.DB.QueryContext(ctx, `SELECT `+instanceColumns+` FROM instances WHERE cluster_id=? AND lifecycle<>'DELETED' ORDER BY activity_sequence DESC,id`, r.ClusterId)
	if e != nil {
		return nil, Error("UNAVAILABLE", "列表不可读")
	}
	all := []*rpc.ObservationInstance{}
	for rows.Next() {
		i, err := scanInstance(rows)
		if err != nil {
			rows.Close()
			return nil, Error("UNAVAILABLE", "列表不可读")
		}
		all = append(all, i)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, Error("UNAVAILABLE", "列表不可读")
	}
	for _, i := range all {
		runtime, e := s.GetRuntime(ctx, c, &rpc.InstanceID{Id: i.Id})
		if e != nil {
			return nil, e
		}
		i.Runtime = runtime
		i.Sources = s.instanceSources(ctx, i)
		projectCurrentVersion(i)
	}
	external, e := s.cachedExternal(ctx, r.ClusterId)
	if e != nil {
		return nil, e
	}
	all = append(all, external...)
	stats := &rpc.InstanceStatistics{}
	for _, i := range all {
		if i.ReadOnly {
			stats.Unmanaged++
		} else {
			stats.Managed++
		}
		if i.Runtime != nil && i.Runtime.Summary == "Available" {
			stats.Available++
		}
	}
	var blocked int64
	if s.DB.QueryRowContext(ctx, `SELECT count(*) FROM operations WHERE cluster_id=? AND action IN ('PUBLISH','DELETE') AND state IN ('FAILED','WAITING_CONFIRMATION','RESULT_UNCERTAIN','IDENTITY_UNCERTAIN','DELETION_VERIFYING','DELETION_BLOCKED')`, r.ClusterId).Scan(&blocked) != nil || blocked > 2147483647 {
		return nil, Error("UNAVAILABLE", "任务统计不可读")
	}
	stats.Blocked = int32(blocked)
	filtered := []*rpc.ObservationInstance{}
	search := strings.ToLower(r.GetName())
	for _, i := range all {
		// 关键：字面子串不经过LIKE/正则，%和_也只是字符；统计使用过滤前的同一授权集合。
		if !strings.Contains(strings.ToLower(i.Name), search) || r.GetType() != "" && i.Type != r.GetType() || r.GetComponent() != "" && i.Component != r.GetComponent() || r.GetSummary() != "" && (i.Runtime == nil || i.Runtime.Summary != r.GetSummary()) {
			continue
		}
		filtered = append(filtered, i)
	}
	if len(filtered) > 2147483647 {
		return nil, Error("UNAVAILABLE", "结果数量超出范围")
	}
	pagination := &rpc.InstancePagination{Number: page, Size: size, Total: int32(len(filtered)), Pages: int32((int64(len(filtered)) + int64(size) - 1) / int64(size))}
	start := int64(page-1) * int64(size)
	items := []*rpc.ObservationInstance{}
	if start < int64(len(filtered)) {
		end := start + int64(size)
		if end > int64(len(filtered)) {
			end = int64(len(filtered))
		}
		items = filtered[start:end]
	}
	// 关键：查询期间失权不能通过迟到列表或统计继续获得旧集群数据。
	if _, e := s.GetCluster(ctx, c, &rpc.ClusterID{Id: r.ClusterId}); e != nil {
		return nil, e
	}
	p, e := s.Authorize(ctx, token(c))
	if e != nil {
		return nil, e
	}
	cl, e := clusterByID(ctx, s.DB, r.ClusterId)
	if e != nil {
		return nil, e
	}
	for _, i := range items {
		projectInstanceAccess(p, cl, i)
	}
	return &rpc.QueryInstancesResult_{Instances: items, Statistics: stats, Page: pagination}, nil
}

// projectCurrentVersion 仅隐藏不可确认的当前入口；不可变历史仍可单独授权查看。
func projectCurrentVersion(i *rpc.ObservationInstance) {
	if i.ReadOnly || i.Uid == "" || i.Lifecycle == "DELETED" || i.Runtime != nil && i.Runtime.Summary == "Missing" {
		i.CurrentVersionId = ""
		return
	}
	for _, source := range i.Sources {
		if source.Source == "CR" && strings.HasPrefix(source.Error, "CONFLICT：") {
			i.CurrentVersionId = ""
		}
	}
}
