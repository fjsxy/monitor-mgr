// 目标读取投影：完整对象仅在内存参与条件更新，持久化与页面只接收脱敏副本。
package control

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"strings"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// targetFingerprint 保留完整配置的比较能力，不持久化来自目标的未支持敏感值。
func targetFingerprint(key []byte, i *rpc.ObservationInstance, normalized string) string {
	a := componentFor(i.Component)
	derive := hmac.New(sha256.New, key)
	derive.Write([]byte("monitor-mgr/preview-fingerprint-key/v1"))
	h := hmac.New(sha256.New, derive.Sum(nil))
	// 关键：域、组件、实例、原UID与配置以结构化编码绑定；RV/status不是配置变化。
	input, _ := json.Marshal([]string{"monitor-mgr/preview-target/v1", i.Id, i.ClusterId, i.Component, a.apiVersion, a.kind, a.scope, instanceCRNamespace(i), i.Name, i.Uid, normalized})
	h.Write(input)
	return "hmac:v1:" + hex.EncodeToString(h.Sum(nil))
}
func (s *Store) fingerprint(i *rpc.ObservationInstance, normalized string) string {
	return targetFingerprint(s.Key, i, normalized)
}

// safeTargetDocument 深复制后才投影，绝不改写随后发往API的metadata/status/finalizers。
func safeTargetDocument(cr map[string]any) map[string]any {
	if cr == nil {
		return nil
	}
	var copy map[string]any
	json.Unmarshal([]byte(canonical(cr)), &copy)
	sanitizeTarget(copy, "")
	return copy
}
func sanitizeTarget(v any, parent string) {
	switch x := v.(type) {
	case map[string]any:
		for k, value := range x {
			lower := strings.ToLower(k)
			// headers是任意名称和值的认证通道，不能按几个常见名称枚举秘密。
			if strings.EqualFold(parent, "headers") {
				x[k] = "[敏感内容已隐藏]"
				continue
			}
			// 关键：原始last-applied可能内嵌整份旧配置；未知认证分支不回显字面值。
			if strings.Contains(lower, "last-applied") || isLiteral(value) && (lower == "bearertoken" || lower == "password" || lower == "token" || lower == "clientsecret" || lower == "credentials" || lower == "username" && parent == "basicAuth") {
				x[k] = "[敏感内容已隐藏]"
				continue
			}
			if lower == "url" || lower == "proxyurl" {
				if raw, ok := value.(string); ok {
					u, e := url.Parse(raw)
					// 关键：外部CR可含schema接受但无法解析的认证URL；不能因解析失败回显原值。
					if e != nil {
						x[k] = "[敏感内容已隐藏]"
					} else if u.User != nil {
						u.User = url.User("[已隐藏]")
						x[k] = u.String()
					}
				}
			}
			sanitizeTarget(x[k], k)
		}
	case []any:
		for _, item := range x {
			sanitizeTarget(item, parent)
		}
	}
}
func isLiteral(v any) bool { _, ok := v.(string); return ok }
