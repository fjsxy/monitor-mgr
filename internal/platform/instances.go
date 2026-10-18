package platform

import (
	"github.com/fjsxy/monitor-mgr/internal/control"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"net/http"
	"strings"
)

func (p *Runtime) instanceRoutes(w http.ResponseWriter, r *http.Request, c *rpc.RequestContext, id string) bool {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/")
	parts := strings.Split(path, "/")
	if len(parts) == 3 && parts[0] == "clusters" && parts[2] == "instances" {
		switch r.Method {
		case "GET":
			v, e := p.client.ListInstances(r.Context(), c, &rpc.ClusterID{Id: parts[1]})
			p.respond(w, 200, v, e, id)
			return true
		case "POST":
			var b struct {
				Component string                `json:"component"`
				Ksm       *rpc.KSMConfiguration `json:"ksm"`
			}
			if e := decode(r, &b); e != nil {
				p.writeError(w, e, id)
				return true
			}
			v, e := p.client.CreateInstance(r.Context(), c, &rpc.CreateInstanceRequest{ClusterId: parts[1], Component: b.Component, Ksm: b.Ksm})
			p.respond(w, 201, v, e, id)
			return true
		}
	}
	if path == "operations" && r.Method == "GET" {
		v, e := p.client.LookupOperations(r.Context(), c, &rpc.OperationLookup{InstanceId: r.URL.Query().Get("instanceId"), IdempotencyKey: r.URL.Query().Get("idempotencyKey")})
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
		case "draft":
			switch r.Method {
			case "GET":
				v, e := p.client.GetDraft(r.Context(), c, req)
				p.respond(w, 200, v, e, id)
				return true
			case "PUT":
				var b struct {
					ExpectedDraftRevision int64                 `json:"expectedDraftRevision"`
					Ksm                   *rpc.KSMConfiguration `json:"ksm"`
				}
				if e := decode(r, &b); e != nil {
					p.writeError(w, e, id)
					return true
				}
				v, e := p.client.SaveDraft(r.Context(), c, &rpc.SaveDraftRequest{Id: req.Id, ExpectedDraftRevision: b.ExpectedDraftRevision, Ksm: b.Ksm})
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
		case "previews":
			if r.Method == "POST" {
				var b struct {
					DraftRevision int64 `json:"draftRevision"`
				}
				if e := decode(r, &b); e != nil {
					p.writeError(w, e, id)
					return true
				}
				v, e := p.client.PreviewPublication(r.Context(), c, &rpc.PreviewRequest{Id: req.Id, DraftRevision: b.DraftRevision})
				p.respond(w, 200, v, e, id)
				return true
			}
		case "publications":
			if r.Method == "POST" {
				var b struct {
					PreviewId      string `json:"previewId"`
					DraftRevision  int64  `json:"draftRevision"`
					OverwriteDrift bool   `json:"overwriteDrift"`
				}
				if e := decode(r, &b); e != nil {
					p.writeError(w, e, id)
					return true
				}
				v, e := p.client.PublishConfiguration(r.Context(), c, &rpc.PublishRequest{Id: req.Id, PreviewId: b.PreviewId, DraftRevision: b.DraftRevision, OverwriteDrift: b.OverwriteDrift})
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
