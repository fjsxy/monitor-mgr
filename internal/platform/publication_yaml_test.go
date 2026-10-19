package platform_test

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

// 比较实际TLS请求与HTTP预览时统一数值类型；失败只输出固定原因，不回显目标敏感内容。
func publicationJSONDocument(t *testing.T, raw []byte) map[string]any {
	t.Helper()
	var out map[string]any
	if json.Unmarshal(raw, &out) != nil || out == nil {
		t.Fatal("发布文档JSON不可读")
	}
	return out
}

func publicationYAMLDocument(t *testing.T, text string) map[string]any {
	t.Helper()
	var out map[string]any
	if yaml.Unmarshal([]byte(text), &out) != nil {
		t.Fatal("发布预览YAML不可读")
	}
	raw, err := json.Marshal(out)
	if err != nil {
		t.Fatal("发布预览无法转为JSON比较")
	}
	return publicationJSONDocument(t, raw)
}

func assertPublicationNonSpecPreserved(t *testing.T, right, before map[string]any) {
	t.Helper()
	for key, value := range before {
		if key != "spec" && !reflect.DeepEqual(right[key], value) {
			t.Fatal("右侧预览未保留原非spec字段")
		}
	}
}

func assertPublicationDocumentEqual(t *testing.T, right, sent map[string]any) {
	t.Helper()
	if !reflect.DeepEqual(right, sent) {
		t.Fatal("右侧预览与实际写请求的安全投影不同")
	}
}

// 首次创建分别验证KSM和Agent，不把已存在CR的保留字段规则带入新对象。
func TestPublicationYAMLFirstCreate(t *testing.T) {
	t.Run("ksm", func(t *testing.T) {
		p, c, id, f := ksmPublishedFixture(t)
		preview := ksmPreview(t, p, c, id, 1)
		right := assertPublicationFirstCreate(t, preview)
		f.mu.Lock()
		created := f.cr != nil || f.writes != 0
		f.mu.Unlock()
		if created {
			t.Fatal("预览提前创建KSM")
		}
		op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "first-create", 202)
		if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
			t.Fatal("KSM首次发布未成功")
		}
		f.mu.Lock()
		sent := append([]byte(nil), f.lastWrite...)
		f.mu.Unlock()
		assertPublicationDocumentEqual(t, right, publicationJSONDocument(t, sent))
	})
	t.Run("agent", func(t *testing.T) {
		p, c, cluster, _, f := agentSafetyFixture(t, nil)
		i := clusterCall(t, p, "POST", "/clusters/"+cluster+"/instances", map[string]any{"component": "Prometheus Agent", "agent": agentConfig("first-preview", "monitoring")}, c, "create", 201)
		id := i["id"].(string)
		preview := clusterCall(t, p, "POST", "/instances/"+id+"/previews", map[string]any{"draftRevision": 1}, c, "preview", 200)
		right := assertPublicationFirstCreate(t, preview)
		f.mu.Lock()
		created := len(f.objects) != 0 || f.lastWrite != nil
		f.mu.Unlock()
		if created {
			t.Fatal("预览提前创建Agent")
		}
		op := clusterCall(t, p, "POST", "/instances/"+id+"/publications", map[string]any{"previewId": preview["previewId"], "draftRevision": 1, "overwriteDrift": false}, c, "first-create", 202)
		if awaitClusterOperation(t, p, c, op["operationId"].(string))["state"] != "SUCCEEDED" {
			t.Fatal("Agent首次发布未成功")
		}
		f.mu.Lock()
		sent := append([]byte(nil), f.lastWrite...)
		f.mu.Unlock()
		assertPublicationDocumentEqual(t, right, publicationJSONDocument(t, sent))
	})
}

func assertPublicationFirstCreate(t *testing.T, preview map[string]any) map[string]any {
	t.Helper()
	if !strings.Contains(preview["leftYAML"].(string), "首次创建：当前不存在 CR") {
		t.Fatal("首次创建未说明原CR不存在")
	}
	right := publicationYAMLDocument(t, preview["rightYAML"].(string))
	meta := right["metadata"].(map[string]any)
	if meta["uid"] != nil || meta["resourceVersion"] != nil || meta["finalizers"] != nil || right["status"] != nil {
		t.Fatal("首次创建预览伪造身份或存量字段")
	}
	return right
}
