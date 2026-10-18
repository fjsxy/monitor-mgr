// HTTP实例适配器只做路径、JSON和状态码转换，全部业务经生成的Kitex client。
package platform

import (
	"github.com/fjsxy/monitor-mgr/internal/control"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"net/http"
	"strconv"
	"strings"
)

func (p *Runtime) instanceRoutes(w http.ResponseWriter, r *http.Request, c *rpc.RequestContext, id string) bool {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/")
	parts := strings.Split(path, "/")
	if len(parts) == 3 && parts[0] == "clusters" && parts[2] == "instances" {
		switch r.Method {
		case "GET":
			if r.URL.RawQuery != "" {
				query, e := instanceQuery(r, parts[1])
				if e != nil {
					p.writeError(w, e, id)
					return true
				}
				v, e := p.client.QueryInstances(r.Context(), c, query)
				p.respond(w, 200, v, e, id)
				return true
			}
			v, e := p.client.ListInstances(r.Context(), c, &rpc.ClusterID{Id: parts[1]})
			p.respond(w, 200, v, e, id)
			return true
		case "POST":
			var b struct {
				Component string                  `json:"component"`
				Ksm       *rpc.KSMConfiguration   `json:"ksm"`
				Agent     *rpc.AgentConfiguration `json:"agent"`
			}
			if e := decode(r, &b); e != nil {
				p.writeError(w, e, id)
				return true
			}
			v, e := p.client.CreateInstance(r.Context(), c, &rpc.CreateInstanceRequest{ClusterId: parts[1], Component: b.Component, Ksm: b.Ksm, Agent: b.Agent})
			p.respond(w, 201, v, e, id)
			return true
		}
	}
	if path == "operations" && r.Method == "GET" {
		v, e := p.client.LookupOperations(r.Context(), c, &rpc.OperationLookup{InstanceId: r.URL.Query().Get("instanceId"), IdempotencyKey: r.URL.Query().Get("idempotencyKey")})
		p.respond(w, 200, v, e, id)
		return true
	}
	if len(parts) == 3 && parts[0] == "operations" && r.Method == "POST" && (parts[2] == "retry" || parts[2] == "cancel" || parts[2] == "continue-unsent") {
		var b struct {
			ExpectedOperationRevision httpI64 `json:"expectedOperationRevision"`
			ExpectedAttemptId         string  `json:"expectedAttemptId"`
			ExpectedAttemptRevision   httpI64 `json:"expectedAttemptRevision"`
			DeletePreviewId           *string `json:"deletePreviewId"`
		}
		if e := decode(r, &b); e != nil {
			p.writeError(w, e, id)
			return true
		}
		req := &rpc.LifecycleRequest{OperationId: parts[1], ExpectedOperationRevision: int64(b.ExpectedOperationRevision), ExpectedAttemptId: b.ExpectedAttemptId, ExpectedAttemptRevision: int64(b.ExpectedAttemptRevision), DeletePreviewId: b.DeletePreviewId}
		var v *rpc.Operation
		var e error
		status := 202
		switch parts[2] {
		case "retry":
			v, e = p.client.RetryLifecycle(r.Context(), c, req)
		case "cancel":
			status = 200
			v, e = p.client.CancelLifecycle(r.Context(), c, req)
		case "continue-unsent":
			v, e = p.client.ContinueLifecycle(r.Context(), c, req)
		}
		p.respond(w, status, v, e, id)
		return true
	}
	if len(parts) == 3 && parts[0] == "operations" && parts[2] == "refresh" && r.Method == "POST" {
		v, e := p.client.RefreshOperation(r.Context(), c, &rpc.OperationID{OperationId: parts[1]})
		p.respond(w, 200, v, e, id)
		return true
	}
	if len(parts) == 3 && parts[0] == "operations" && parts[2] == "attempts" && r.Method == "GET" {
		v, e := p.client.GetAttempts(r.Context(), c, &rpc.OperationID{OperationId: parts[1]})
		p.respond(w, 200, v, e, id)
		return true
	}
	if len(parts) < 2 || parts[0] != "instances" {
		return false
	}
	req := &rpc.InstanceID{Id: parts[1]}
	if len(parts) == 2 {
		switch r.Method {
		case "GET":
			v, e := p.client.GetInstance(r.Context(), c, req)
			p.respond(w, 200, v, e, id)
			return true
		case "DELETE":
			v, e := p.client.DeleteDraft(r.Context(), c, req)
			p.respond(w, 200, v, e, id)
			return true
		}
	}
	if len(parts) == 3 {
		switch parts[2] {
		case "deletion-previews":
			if r.Method == "POST" {
				var b struct {
					OperationId *string `json:"operationId"`
				}
				if e := decode(r, &b); e != nil {
					p.writeError(w, e, id)
					return true
				}
				v, e := p.client.PreviewDeletion(r.Context(), c, &rpc.DeletionPreviewRequest{Id: req.Id, OperationId: b.OperationId})
				p.respond(w, 200, v, e, id)
				return true
			}
		case "deletions":
			if r.Method == "POST" {
				var b struct {
					VersionId string  `json:"versionId"`
					PreviewId *string `json:"previewId"`
				}
				if e := decode(r, &b); e != nil {
					p.writeError(w, e, id)
					return true
				}
				v, e := p.client.DeleteInstance(r.Context(), c, &rpc.DeleteInstanceRequest{Id: req.Id, VersionId: b.VersionId, PreviewId: b.PreviewId})
				p.respond(w, 202, v, e, id)
				return true
			}
		case "draft":
			switch r.Method {
			case "GET":
				v, e := p.client.GetDraft(r.Context(), c, req)
				p.respond(w, 200, v, e, id)
				return true
			case "PUT":
				var b struct {
					ExpectedDraftRevision httpI64                 `json:"expectedDraftRevision"`
					Ksm                   *rpc.KSMConfiguration   `json:"ksm"`
					Agent                 *rpc.AgentConfiguration `json:"agent"`
				}
				if e := decode(r, &b); e != nil {
					p.writeError(w, e, id)
					return true
				}
				v, e := p.client.SaveDraft(r.Context(), c, &rpc.SaveDraftRequest{Id: req.Id, ExpectedDraftRevision: int64(b.ExpectedDraftRevision), Ksm: b.Ksm, Agent: b.Agent})
				p.respond(w, 200, v, e, id)
				return true
			}
		case "copy-template":
			if r.Method == "GET" {
				v, e := p.client.CopyTemplate(r.Context(), c, req)
				p.respond(w, 200, v, e, id)
				return true
			}
		case "editing-activity":
			if r.Method == "POST" {
				v, e := p.client.RecordEditingActivity(r.Context(), c, req)
				p.respond(w, 200, v, e, id)
				return true
			}
		case "association-previews":
			if r.Method == "POST" {
				var body struct {
					DraftRevision httpI64 `json:"draftRevision"`
				}
				if e := decode(r, &body); e != nil {
					p.writeError(w, e, id)
					return true
				}
				v, e := p.client.PreviewAssociations(r.Context(), c, &rpc.AssociationPreviewRequest{Id: req.Id, DraftRevision: int64(body.DraftRevision)})
				p.respond(w, 200, v, e, id)
				return true
			}
		case "previews":
			if r.Method == "POST" {
				var b struct {
					DraftRevision httpI64 `json:"draftRevision"`
				}
				if e := decode(r, &b); e != nil {
					p.writeError(w, e, id)
					return true
				}
				v, e := p.client.PreviewPublication(r.Context(), c, &rpc.PreviewRequest{Id: req.Id, DraftRevision: int64(b.DraftRevision)})
				p.respond(w, 200, v, e, id)
				return true
			}
		case "publications":
			if r.Method == "POST" {
				var b struct {
					PreviewId      string  `json:"previewId"`
					DraftRevision  httpI64 `json:"draftRevision"`
					OverwriteDrift bool    `json:"overwriteDrift"`
				}
				if e := decode(r, &b); e != nil {
					p.writeError(w, e, id)
					return true
				}
				v, e := p.client.PublishConfiguration(r.Context(), c, &rpc.PublishRequest{Id: req.Id, PreviewId: b.PreviewId, DraftRevision: int64(b.DraftRevision), OverwriteDrift: b.OverwriteDrift})
				p.respond(w, 202, v, e, id)
				return true
			}
		case "runtime":
			if r.Method == "GET" {
				v, e := p.client.GetRuntime(r.Context(), c, req)
				p.respond(w, 200, v, e, id)
				return true
			}
		case "refresh":
			if r.Method == "POST" {
				v, e := p.client.RefreshInstance(r.Context(), c, req)
				p.respond(w, 200, v, e, id)
				return true
			}
		case "versions":
			if r.Method == "GET" {
				v, e := p.client.ListVersions(r.Context(), c, req)
				p.respond(w, 200, v, e, id)
				return true
			}
		}
	}
	if len(parts) == 4 && parts[2] == "versions" && r.Method == "GET" {
		v, e := p.client.GetVersion(r.Context(), c, &rpc.VersionRequest{Id: req.Id, VersionId: parts[3]})
		p.respond(w, 200, v, e, id)
		return true
	}
	p.writeError(w, control.Error("NOT_FOUND", "入口不存在"), id)
	return true
}

// instanceQuery 仅转换HTTP查询参数；授权、筛选与统计始终在真实RPC应用层执行。
func instanceQuery(r *http.Request, cluster string) (*rpc.QueryInstancesRequest, error) {
	q := r.URL.Query()
	request := &rpc.QueryInstancesRequest{ClusterId: cluster}
	for key, values := range q {
		if len(values) != 1 {
			return nil, control.Error("BAD_REQUEST", "查询参数重复")
		}
		value := values[0]
		switch key {
		case "name":
			request.Name = &value
		case "type":
			request.Type = &value
		case "component":
			request.Component = &value
		case "summary":
			request.Summary = &value
		case "page", "pageSize":
			n, e := strconv.ParseInt(value, 10, 32)
			if e != nil {
				return nil, control.Error("BAD_REQUEST", "分页参数无效")
			}
			number := int32(n)
			if key == "page" {
				request.Page = &number
			} else {
				request.PageSize = &number
			}
		default:
			return nil, control.Error("BAD_REQUEST", "查询参数不支持")
		}
	}
	return request, nil
}
