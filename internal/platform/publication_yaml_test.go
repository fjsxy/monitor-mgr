package platform_test

import (
	"encoding/json"
	"reflect"
	"strings"

	ginkgo "github.com/onsi/ginkgo/v2"
	"gopkg.in/yaml.v3"
)

// 比较实际TLS请求与HTTP预览时统一数值类型；失败只输出固定原因，不回显目标敏感内容。
func publicationJSONDocument(t ginkgo.GinkgoTInterface, raw []byte) map[string]any {
	t.Helper()
	var out map[string]any
	if json.Unmarshal(raw, &out) != nil || out == nil {
		t.Fatal("发布文档JSON不可读")
	}
	return out
}

func publicationYAMLDocument(t ginkgo.GinkgoTInterface, text string) map[string]any {
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

func assertPublicationNonSpecPreserved(t ginkgo.GinkgoTInterface, right, before map[string]any) {
	t.Helper()
	for key, value := range before {
		if key != "spec" && !reflect.DeepEqual(right[key], value) {
			t.Fatal("右侧预览未保留原非spec字段")
		}
	}
}

func assertPublicationDocumentEqual(t ginkgo.GinkgoTInterface, right, sent map[string]any) {
	t.Helper()
	if !reflect.DeepEqual(right, sent) {
		t.Fatal("右侧预览与实际写请求的安全投影不同")
	}
}

// 首次创建分别验证KSM和Agent，不把已存在CR的保留字段规则带入新对象。

func assertPublicationFirstCreate(t ginkgo.GinkgoTInterface, preview map[string]any) map[string]any {
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
