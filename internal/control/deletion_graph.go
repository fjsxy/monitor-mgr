// 原资源图共享正式 KSM 类型及 UID 归属；只读核验不复制 Operator 的名称、选择器或端口算法。
package control

import (
	"context"
	"net/url"
)

type graphResourceKind struct {
	api, kind, resource string
	cluster, optional   bool
}

var workloadGraphKinds = []graphResourceKind{
	{"apps/v1", "Deployment", "deployments", false, false},
	{"apps/v1", "StatefulSet", "statefulsets", false, false},
	{"apps/v1", "DaemonSet", "daemonsets", false, false},
	{"apps/v1", "ReplicaSet", "replicasets", false, false},
	{"v1", "Pod", "pods", false, false},
}

// ksmGraphKinds 对应锁定 RC4 正式直接受管类型及其工作负载/Service 后代；集群资源不拼 namespace。
var ksmGraphKinds = append(append([]graphResourceKind{}, workloadGraphKinds...),
	graphResourceKind{"v1", "Service", "services", false, false},
	graphResourceKind{"v1", "ServiceAccount", "serviceaccounts", false, false},
	graphResourceKind{"v1", "Endpoints", "endpoints", false, false},
	graphResourceKind{"discovery.k8s.io/v1", "EndpointSlice", "endpointslices", false, false},
	graphResourceKind{"rbac.authorization.k8s.io/v1", "Role", "roles", false, false},
	graphResourceKind{"rbac.authorization.k8s.io/v1", "RoleBinding", "rolebindings", false, false},
	graphResourceKind{"rbac.authorization.k8s.io/v1", "ClusterRole", "clusterroles", true, false},
	graphResourceKind{"rbac.authorization.k8s.io/v1", "ClusterRoleBinding", "clusterrolebindings", true, false},
)

var serviceMonitorGraphKind = graphResourceKind{serviceMonitorAPI, "ServiceMonitor", "servicemonitors", false, true}

// listGraphCandidates 只在全部必要列表及分页成功后返回；可选 SM API 首页404与读取未知分别处理。
func (t *Target) listGraphCandidates(ctx context.Context, namespace string, kinds []graphResourceKind) ([]seenResource, error) {
	out := []seenResource{}
	for _, kind := range kinds {
		base := "/apis/" + kind.api
		if kind.api == "v1" {
			base = "/api/v1"
		}
		path := base
		if !kind.cluster && namespace != "" {
			path += "/namespaces/" + url.PathEscape(namespace)
		}
		objects, e := t.listObjectsWithOptionalAPI(ctx, path+"/"+kind.resource, kind.optional)
		if e != nil {
			return nil, e
		}
		for _, object := range objects {
			meta := objectMap(object, "metadata")
			uid, name, ns := objectString(meta, "uid"), objectString(meta, "name"), objectString(meta, "namespace")
			if !kind.cluster && ns == "" {
				ns = namespace
			}
			if uid == "" || name == "" || (!kind.cluster && ns == "") || (kind.cluster && ns != "") || (namespace != "" && !kind.cluster && namespace != ns) {
				return nil, Error("UNAVAILABLE", "原资源图身份或作用域不完整")
			}
			objectPath := base
			if !kind.cluster {
				objectPath += "/namespaces/" + url.PathEscape(ns)
			}
			objectPath += "/" + kind.resource + "/" + url.PathEscape(name)
			out = append(out, seenResource{kind.api, kind.kind, ns, name, uid, ownerUID(meta), objectPath})
		}
	}
	return out, nil
}

// readSeenGraph 保留路径与 UID 的绑定，不能只取 UID 后把同名替代解释为旧图已清空。
func readSeenGraph(ctx context.Context, q clusterQuery, instance string) ([]seenResource, error) {
	rows, e := q.QueryContext(ctx, `SELECT api_version,kind,namespace,name,uid,owner_uid,path FROM seen_resources WHERE instance_id=?`, instance)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []seenResource{}
	for rows.Next() {
		var resource seenResource
		if e = rows.Scan(&resource.apiVersion, &resource.kind, &resource.namespace, &resource.name, &resource.uid, &resource.ownerUID, &resource.path); e != nil {
			return nil, e
		}
		out = append(out, resource)
	}
	return out, rows.Err()
}

// originalOwnerGraph 关键：只从原父 UID 和持久原 UID 求闭包；原父之外的同路径替代永不认领，也不能解除保护。
func originalOwnerGraph(uid string, durable, candidates []seenResource) ([]seenResource, error) {
	seen := map[string]bool{uid: true}
	paths := map[string]string{}
	for _, resource := range durable {
		seen[resource.uid] = true
		paths[resource.path] = resource.uid
	}
	for changed := true; changed; {
		changed = false
		for _, resource := range candidates {
			if !seen[resource.uid] && seen[resource.ownerUID] {
				seen[resource.uid] = true
				changed = true
			}
		}
	}
	// 活跃原父可合法重建子对象；只有原 UID 闭包之外的同路径替代不能被认领或作为清空证据。
	for _, resource := range candidates {
		if original := paths[resource.path]; original != "" && original != resource.uid && !seen[resource.uid] {
			return nil, Error("CONFLICT", "原资源图路径出现替代 UID")
		}
	}
	live := []seenResource{}
	for _, resource := range candidates {
		if seen[resource.uid] {
			live = append(live, resource)
		}
	}
	return live, nil
}
