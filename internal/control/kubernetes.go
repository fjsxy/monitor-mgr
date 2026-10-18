package control

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"gopkg.in/yaml.v3"
)

// targetCredential 保存所选 context 的内嵌材料，只供加密存储和目标客户端使用。
// 关键：不从外部文件、环境 proxy、插件或 Pod ServiceAccount 补充身份，避免隐式越权。
type targetCredential struct {
	Server     string `json:"server"`
	CA         []byte `json:"ca"`
	Cert       []byte `json:"cert,omitempty"`
	Key        []byte `json:"key,omitempty"`
	Token      string `json:"token,omitempty"`
	ServerName string `json:"serverName,omitempty"`
}

// kubeFile 只解析核验所选 context 所需字段，不执行 kubeconfig loader。
type kubeFile struct {
	Clusters []struct {
		Name    string `yaml:"name"`
		Cluster struct {
			Server     string `yaml:"server"`
			CA         string `yaml:"certificate-authority-data"`
			CAFile     string `yaml:"certificate-authority"`
			Insecure   bool   `yaml:"insecure-skip-tls-verify"`
			Proxy      string `yaml:"proxy-url"`
			ServerName string `yaml:"tls-server-name"`
		} `yaml:"cluster"`
	} `yaml:"clusters"`
	Users []struct {
		Name string `yaml:"name"`
		User struct {
			Cert         string `yaml:"client-certificate-data"`
			Key          string `yaml:"client-key-data"`
			CertFile     string `yaml:"client-certificate"`
			KeyFile      string `yaml:"client-key"`
			Token        string `yaml:"token"`
			TokenFile    string `yaml:"tokenFile"`
			Exec         any    `yaml:"exec"`
			AuthProvider any    `yaml:"auth-provider"`
			Username     string `yaml:"username"`
			Password     string `yaml:"password"`
		} `yaml:"user"`
	} `yaml:"users"`
	Contexts []struct {
		Name    string `yaml:"name"`
		Context struct {
			Cluster string `yaml:"cluster"`
			User    string `yaml:"user"`
		} `yaml:"context"`
	} `yaml:"contexts"`
}

// parseCredential 静态验证所选 context，并提取允许的内嵌 HTTPS 凭据。
// 关键：先拒绝外部文件、插件和 proxy，再构造客户端，防止上传配置触发额外访问或执行。
func parseCredential(raw, contextName string) (*targetCredential, error) {
	if len(raw) > 131072 || strings.TrimSpace(contextName) == "" {
		return nil, Error("INVALID_INPUT", "须上传内嵌 kubeconfig 并明确选择 context")
	}
	var cfg kubeFile
	dec := yaml.NewDecoder(strings.NewReader(raw))
	if dec.Decode(&cfg) != nil {
		return nil, Error("INVALID_INPUT", "kubeconfig 格式无效")
	}
	var extra any
	if dec.Decode(&extra) != io.EOF {
		return nil, Error("INVALID_INPUT", "只接受一个 kubeconfig 文档")
	}
	clusterName, userName := "", ""
	count := 0
	for _, c := range cfg.Contexts {
		if c.Name == contextName {
			clusterName = c.Context.Cluster
			userName = c.Context.User
			count++
		}
	}
	if count != 1 {
		return nil, Error("INVALID_INPUT", "所选 context 不存在或重复")
	}
	result := &targetCredential{}
	count = 0
	for _, c := range cfg.Clusters {
		if c.Name != clusterName {
			continue
		}
		count++
		v := c.Cluster
		u, e := url.Parse(v.Server)
		if e != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
			return nil, Error("INVALID_INPUT", "API endpoint 必须为 HTTPS")
		}
		if v.Insecure || v.CAFile != "" || v.Proxy != "" {
			return nil, Error("INVALID_INPUT", "不支持跳过 TLS、外部 CA 或 proxy")
		}
		result.Server = strings.TrimRight(v.Server, "/")
		result.ServerName = v.ServerName
		result.CA, e = base64.StdEncoding.DecodeString(v.CA)
		if e != nil || len(result.CA) == 0 {
			return nil, Error("INVALID_INPUT", "必须提供内嵌 CA")
		}
	}
	if count != 1 {
		return nil, Error("INVALID_INPUT", "所选 cluster 不存在或重复")
	}
	count = 0
	for _, u := range cfg.Users {
		if u.Name != userName {
			continue
		}
		count++
		v := u.User
		if v.CertFile != "" || v.KeyFile != "" || v.TokenFile != "" || v.Exec != nil || v.AuthProvider != nil || v.Username != "" || v.Password != "" {
			return nil, Error("INVALID_INPUT", "不支持外部凭据、tokenFile、exec、auth-provider 或用户名密码")
		}
		if (v.Cert == "") != (v.Key == "") || (v.Token != "" && v.Cert != "") || (v.Token == "" && v.Cert == "") {
			return nil, Error("INVALID_INPUT", "须提供内嵌 cert/key 或 token")
		}
		var e error
		result.Token = v.Token
		if strings.ContainsAny(v.Token, "\r\n") {
			return nil, Error("INVALID_INPUT", "token 格式无效")
		}
		if result.Cert, e = base64.StdEncoding.DecodeString(v.Cert); e != nil {
			return nil, Error("INVALID_INPUT", "内嵌证书无效")
		}
		if result.Key, e = base64.StdEncoding.DecodeString(v.Key); e != nil {
			return nil, Error("INVALID_INPUT", "内嵌私钥无效")
		}
	}
	if count != 1 {
		return nil, Error("INVALID_INPUT", "所选 user 不存在或重复")
	}
	if _, e := newTarget(result); e != nil {
		return nil, e
	}
	return result, nil
}

// Target 提供有预算与 TLS 校验的目标 API 访问，错误不回显上传材料。
type Target struct {
	credential         *targetCredential
	client             *http.Client
	limits             *targetLimits
	clusterID          string
	credentialRevision int64
	clusterUID         string
}

// newTarget 仅使用上传的 CA 与身份材料建立客户端，保持 TLS 校验并拒绝重定向。
func newTarget(c *targetCredential) (*Target, error) {
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(c.CA) {
		return nil, Error("INVALID_INPUT", "内嵌 CA 无效")
	}
	cfg := &tls.Config{RootCAs: pool, ServerName: c.ServerName, MinVersion: tls.VersionTLS12}
	if len(c.Cert) > 0 {
		cert, e := tls.X509KeyPair(c.Cert, c.Key)
		if e != nil {
			return nil, Error("INVALID_INPUT", "内嵌 cert/key 无效")
		}
		cfg.Certificates = []tls.Certificate{cert}
	}
	return &Target{credential: c, client: &http.Client{Transport: &http.Transport{TLSClientConfig: cfg, Proxy: nil}, Timeout: 8 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("API 重定向被拒绝") }}}, nil
}

// Close 释放目标连接池中的空闲连接。
func (t *Target) Close() { t.client.CloseIdleConnections() }

// Request 在目标预算内执行一次 API 请求，将失败转换为不含上传材料的业务错误。
func (t *Target) Request(ctx context.Context, method, path string, body any, out any) error {
	if method == "GET" && body == nil && t.limits != nil {
		credential, _ := json.Marshal(t.credential)
		digest := sha256.Sum256(credential)
		key := fmt.Sprintf("%s/%s/%d/%x/%s", t.clusterID, t.clusterUID, t.credentialRevision, digest, path)
		raw, e := t.limits.read(ctx, key, func() ([]byte, error) {
			var text targetText
			e := t.request(ctx, method, path, nil, &text, nil)
			return []byte(text), e
		})
		if e != nil {
			return e
		}
		if out == nil {
			return nil
		}
		if text, ok := out.(*targetText); ok {
			*text = targetText(raw)
			return nil
		}
		if json.Unmarshal(raw, out) != nil {
			return Error("TARGET_ERROR", "目标 API 响应无效")
		}
		return nil
	}
	return t.request(ctx, method, path, body, out, nil)
}

// MutationOutcome 不把已发后断连或成功 HTTP 后解码失败当成明确拒绝。
type MutationOutcome struct {
	Object     map[string]any
	HTTPStatus int
	Rejected   bool
	Err        error
}

func (t *Target) Mutate(ctx context.Context, method, path string, body any) MutationOutcome {
	o := MutationOutcome{}
	o.Err = t.request(ctx, method, path, body, &o.Object, &o.HTTPStatus)
	switch o.HTTPStatus {
	case 401, 403, 404, 409, 422:
		o.Rejected = true
	}
	return o
}
func (t *Target) request(ctx context.Context, method, path string, body any, out any, status *int) error {
	// 关键：排队与网络共用原调用的剩余预算，不能借等待或重试突破目标请求上限。
	bounded, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	ctx = bounded
	if t.limits != nil {
		release, e := t.limits.acquire(ctx, t.clusterID)
		if e != nil {
			return e
		}
		defer release()
	}

	var b []byte
	if body != nil {
		b, _ = json.Marshal(body)
	}
	req, e := http.NewRequestWithContext(ctx, method, t.credential.Server+path, bytes.NewReader(b))
	if e != nil {
		return Error("TARGET_UNAVAILABLE", "目标 API 请求无效")
	}
	req.Header.Set("Content-Type", "application/json")
	if t.credential.Token != "" {
		req.Header.Set("Authorization", "Bearer "+t.credential.Token)
	}
	res, e := t.client.Do(req)
	if e != nil {
		var certErr *tls.CertificateVerificationError
		if errors.As(e, &certErr) {
			return Error("TARGET_TLS", "目标 TLS CA 或名称核验失败")
		}
		return Error("TARGET_UNAVAILABLE", "目标 API 无法连接或读取超时")
	}
	defer res.Body.Close()
	if status != nil {
		*status = res.StatusCode
	}
	if res.StatusCode < 200 || res.StatusCode >= 300 {
		code := "TARGET_ERROR"
		msg := "目标 API 返回错误"
		if res.StatusCode == 409 {
			code = "TARGET_CONFLICT"
			msg = "目标资源版本或身份冲突"
		}
		if res.StatusCode == 422 {
			code = "TARGET_REJECTED"
			msg = "目标 API 明确拒绝配置"
		}
		if res.StatusCode == 403 {
			code = "TARGET_FORBIDDEN"
			msg = "目标 API 权限不足"
		}
		if res.StatusCode == 401 {
			code = "TARGET_UNAUTHENTICATED"
			msg = "目标 API 身份凭据无效"
		}
		if res.StatusCode == 404 {
			code = "TARGET_NOT_FOUND"
			msg = "目标 API 或资源不存在"
		}
		return Error(code, msg)
	}
	if out == nil {
		return nil
	}
	if text, ok := out.(*targetText); ok {
		raw, e := io.ReadAll(io.LimitReader(res.Body, 8<<20))
		if e != nil {
			return Error("TARGET_ERROR", "目标文本响应读取失败")
		}
		*text = targetText(raw)
		return nil
	}
	if json.NewDecoder(io.LimitReader(res.Body, 8<<20)).Decode(out) != nil {
		return Error("TARGET_ERROR", "目标 API 响应无效")
	}
	return nil
}

// verifiedTarget 保存已核验的集群身份与本次基础组件能力。
type verifiedTarget struct {
	uid, identity, version string
	capabilities           []*rpc.ClusterCapability
}

// verify 依次核验实际访问身份、集群 UID 和基础能力，不创建业务资源。
func (t *Target) verify(ctx context.Context) (*verifiedTarget, error) {
	// 关键：必须由非匿名 SelfSubjectReview 证明身份，version 或权限审查成功不能替代。
	var ssr struct {
		Status struct {
			UserInfo struct {
				Username string   `json:"username"`
				Groups   []string `json:"groups"`
			} `json:"userInfo"`
		} `json:"status"`
	}
	if e := t.Request(ctx, "POST", "/apis/authentication.k8s.io/v1/selfsubjectreviews", map[string]string{"apiVersion": "authentication.k8s.io/v1", "kind": "SelfSubjectReview"}, &ssr); e != nil {
		return nil, e
	}
	u := ssr.Status.UserInfo
	if u.Username == "" || u.Username == "system:anonymous" {
		return nil, Error("TARGET_UNAUTHENTICATED", "SelfSubjectReview 未核验非匿名身份")
	}
	for _, g := range u.Groups {
		if g == "system:unauthenticated" {
			return nil, Error("TARGET_UNAUTHENTICATED", "SelfSubjectReview 返回匿名身份")
		}
	}
	// 关键：kube-system Namespace UID 是关联锚点，只有取得它才允许切换有效凭据。
	var ns struct {
		Metadata struct {
			UID string `json:"uid"`
		} `json:"metadata"`
	}
	if e := t.Request(ctx, "GET", "/api/v1/namespaces/kube-system", nil, &ns); e != nil {
		return nil, e
	}
	if ns.Metadata.UID == "" {
		return nil, Error("TARGET_ERROR", "无法核验 kube-system Namespace UID")
	}
	var v struct {
		GitVersion string `json:"gitVersion"`
	}
	version := "未知"
	if e := t.Request(ctx, "GET", "/version", nil, &v); e == nil && v.GitVersion != "" {
		version = v.GitVersion
	}
	// 组件能力不足允许受限登记；实际发布仍需按 namespace、动作和依赖再次核验。
	caps := []*rpc.ClusterCapability{}
	for _, c := range []struct{ name, group, version, resource string }{{"kube-state-metrics", "monitoring.fjsxy.github.io", "v1alpha1", "kubestatemetrics"}, {"Prometheus Agent", "monitoring.coreos.com", "v1alpha1", "prometheusagents"}} {
		cap := &rpc.ClusterCapability{Component: c.name, Allowed: true, Reason: "注册时基础能力通过；发布前按目标 namespace、动作和依赖重新核验"}
		var list any
		if e := t.Request(ctx, "GET", "/apis/"+c.group+"/"+c.version+"/"+c.resource+"?limit=1", nil, &list); e != nil {
			cap.Allowed = false
			cap.Reason = "CRD 或读取权限不可用"
		} else if c.name == "kube-state-metrics" && !strings.HasPrefix(version, "v1.28.") {
			cap.Allowed = false
			cap.Reason = "KSM 下发限定 Kubernetes 1.28.x"
		} else {
			for _, verb := range []string{"get", "list", "create", "update", "delete"} {
				var review struct {
					Status struct {
						Allowed bool `json:"allowed"`
					} `json:"status"`
				}
				body := map[string]any{"apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectAccessReview", "spec": map[string]any{"resourceAttributes": map[string]string{"group": c.group, "resource": c.resource, "verb": verb}}}
				if e := t.Request(ctx, "POST", "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews", body, &review); e != nil || !review.Status.Allowed {
					cap.Allowed = false
					cap.Reason = "业务权限不足：" + verb
					break
				}
			}
		}
		caps = append(caps, cap)
	}
	return &verifiedTarget{ns.Metadata.UID, u.Username, version, caps}, nil
}

// Namespaces 完整读取 namespace 分页；任一页失败均不返回伪装完整的部分列表。
func (t *Target) Namespaces(ctx context.Context) ([]string, error) {
	result := []string{}
	cont := ""
	for {
		var list struct {
			Metadata struct {
				Continue string `json:"continue"`
			} `json:"metadata"`
			Items []struct {
				Metadata struct {
					Name string `json:"name"`
				} `json:"metadata"`
			} `json:"items"`
		}
		e := t.Request(ctx, "GET", "/api/v1/namespaces?limit=500&continue="+url.QueryEscape(cont), nil, &list)
		if e != nil {
			return nil, e
		}
		for _, i := range list.Items {
			result = append(result, i.Metadata.Name)
		}
		if list.Metadata.Continue == "" {
			break
		}
		if list.Metadata.Continue == cont {
			return nil, Error("TARGET_ERROR", "namespace 分页未前进")
		}
		cont = list.Metadata.Continue
	}
	sort.Strings(result)
	return result, nil
}

// ListKubeconfigContexts 仅向当前管理员列出 context 名称，不返回凭据或选择默认身份。
func (s *Store) ListKubeconfigContexts(ctx context.Context, c *rpc.RequestContext, r *rpc.KubeconfigRequest) (*rpc.ContextsResult_, error) {
	p, e := s.Authorize(ctx, token(c))
	if e != nil {
		return nil, e
	}
	if e = requireAdmin(p); e != nil {
		return nil, e
	}
	if len(r.Kubeconfig) > 131072 {
		return nil, Error("INVALID_INPUT", "kubeconfig 过大")
	}
	var cfg kubeFile
	if yaml.Unmarshal([]byte(r.Kubeconfig), &cfg) != nil {
		return nil, Error("INVALID_INPUT", "kubeconfig 格式无效")
	}
	names := []string{}
	seen := map[string]bool{}
	for _, c := range cfg.Contexts {
		if c.Name == "" || seen[c.Name] {
			return nil, Error("INVALID_INPUT", "context 名称无效或重复")
		}
		seen[c.Name] = true
		names = append(names, c.Name)
	}
	if len(names) == 0 {
		return nil, Error("INVALID_INPUT", "kubeconfig 没有 context")
	}
	return &rpc.ContextsResult_{Contexts: names}, nil
}

// targetText仅用于受控只读运行指标，不用于凭据或目标CR持久化。
type targetText string
