package platform_test

import (
	"strings"

	ginkgo "github.com/onsi/ginkgo/v2"
)

// REG18-SHARED-003：从外部API返回的秘密也不能进入平台发现/YAML读模型。

// REG18-SHARED-001：真实v4旧库预览必须升级成安全指纹，且原previewId仍能发布。

// REG18-SHARED-003：隐藏秘密仍须参与配置变化判定；仅RV/status变化不能使预览过期。
// 此断言阶段由合并后的业务场景调用；复用已有受控目标与协议夹具。
func checkTestSharedHMACDetectsHiddenConfigAndPreservesMetadata(t ginkgo.GinkgoTInterface) {
	// 接下来REG18-SHARED-003：隐藏秘密仍须参与配置变化判定；仅RV/status变化不能使预览过期。
	ginkgo.By("REG18-SHARED-003：隐藏秘密仍须参与配置变化判定；仅RV/status变化不能使预览过期。")

	p, c, id, f := ksmPublishedFixture(t)
	first := ksmPreview(t, p, c, id, 1)
	op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": first["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "initial", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("前置发布失败")
	}
	f.mu.Lock()
	f.cr["spec"].(map[string]any)["bearerToken"] = "hidden-original-value"
	f.cr["metadata"].(map[string]any)["annotations"] = map[string]any{"kubectl.kubernetes.io/last-applied-configuration": "preserved-sensitive-annotation"}
	f.mu.Unlock()
	preview := ksmPreview(t, p, c, id, 1)
	if preview["drift"] != true || strings.Contains(preview["leftYAML"].(string), "hidden-original-value") || strings.Contains(preview["leftYAML"].(string), "preserved-sensitive-annotation") {
		t.Fatal("漂移预览未安全投影")
	}
	f.mu.Lock()
	f.cr["spec"].(map[string]any)["bearerToken"] = "hidden-changed-value"
	f.mu.Unlock()
	clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": true}, c, "changed-secret", 409)
	preview = ksmPreview(t, p, c, id, 1)
	f.mu.Lock()
	f.cr["metadata"].(map[string]any)["resourceVersion"] = "rv-only"
	f.cr["status"] = map[string]any{"availablePods": float64(0)}
	f.mu.Unlock()
	op = clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": true}, c, "rv-only", 202)
	if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
		t.Fatal("仅RV/status变化使预览失效")
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.cr["metadata"].(map[string]any)["annotations"].(map[string]any)["kubectl.kubernetes.io/last-applied-configuration"] != "preserved-sensitive-annotation" {
		t.Fatal("脱敏改写了目标metadata")
	}
}
