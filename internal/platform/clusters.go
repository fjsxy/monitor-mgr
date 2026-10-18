package platform

import (
	"github.com/fjsxy/monitor-mgr/internal/control"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"net/http"
	"strings"
)

func (p *Runtime) clusterRoutes(w http.ResponseWriter, r *http.Request, c *rpc.RequestContext, id string) {
	if r.URL.Path == "/api/v1/clusters" {
		switch r.Method {
		case "GET":
			v, e := p.client.ListClusters(r.Context(), c)
			p.respond(w, 200, v, e, id)
			return
		case "POST":
			var b rpc.RegisterClusterRequest
			if e := decode(r, &b); e != nil {
				p.writeError(w, e, id)
				return
			}
			v, e := p.client.RegisterCluster(r.Context(), c, &b)
			p.respond(w, 202, v, e, id)
			return
		}
	}
	if strings.HasPrefix(r.URL.Path, "/api/v1/operations/") {
		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/api/v1/operations/"), "/")
		req := &rpc.OperationID{OperationId: parts[0]}
		if len(parts) == 1 && r.Method == "GET" {
			v, e := p.client.GetOperation(r.Context(), c, req)
			p.respond(w, 200, v, e, id)
			return
		}
		if len(parts) == 2 && parts[1] == "continue" && r.Method == "POST" {
			v, e := p.client.ContinueOperation(r.Context(), c, req)
			p.respond(w, 202, v, e, id)
			return
		}
	}
	if strings.HasPrefix(r.URL.Path, "/api/v1/clusters/") {
		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/api/v1/clusters/"), "/")
		req := &rpc.ClusterID{Id: parts[0]}
		if len(parts) == 1 && r.Method == "GET" {
			v, e := p.client.GetCluster(r.Context(), c, req)
			p.respond(w, 200, v, e, id)
			return
		}
		if len(parts) == 2 {
			switch parts[1] {
			case "credentials", "reactivate":
				if r.Method == "POST" {
					var b struct {
						Context    string  `json:"context"`
						Kubeconfig string  `json:"kubeconfig"`
						OwnerId    *string `json:"ownerId"`
					}
					if e := decode(r, &b); e != nil {
						p.writeError(w, e, id)
						return
					}
					in := &rpc.CredentialRequest{Id: req.Id, Context: b.Context, Kubeconfig: b.Kubeconfig, OwnerId: b.OwnerId}
					if parts[1] == "credentials" {
						v, e := p.client.UpdateCredential(r.Context(), c, in)
						p.respond(w, 202, v, e, id)
					} else {
						v, e := p.client.ReactivateCluster(r.Context(), c, in)
						p.respond(w, 202, v, e, id)
					}
					return
				}
			case "transfer":
				if r.Method == "POST" {
					var b struct {
						OwnerId string `json:"ownerId"`
					}
					if e := decode(r, &b); e != nil {
						p.writeError(w, e, id)
						return
					}
					v, e := p.client.TransferCluster(r.Context(), c, &rpc.TransferClusterRequest{Id: req.Id, OwnerId: b.OwnerId})
					p.respond(w, 200, v, e, id)
					return
				}
			case "unregister":
				if r.Method == "POST" {
					v, e := p.client.UnregisterCluster(r.Context(), c, req)
					p.respond(w, 200, v, e, id)
					return
				}
			case "operations":
				if r.Method == "GET" {
					v, e := p.client.ListClusterOperations(r.Context(), c, req)
					p.respond(w, 200, v, e, id)
					return
				}
			case "namespaces":
				if r.Method == "GET" {
					v, e := p.client.ListNamespaces(r.Context(), c, req)
					p.respond(w, 200, v, e, id)
					return
				}
			case "references":
				if r.Method == "GET" {
					v, e := p.client.ListReferences(r.Context(), c, &rpc.ReferencesRequest{Id: req.Id, Kind: r.URL.Query().Get("kind"), Namespace: r.URL.Query().Get("namespace")})
					p.respond(w, 200, v, e, id)
					return
				}
			}
		}
	}
	p.writeError(w, control.Error("NOT_FOUND", "入口不存在"), id)
}
