// HTTP与JavaScript共享JSON边界：安全整数保持number，其余i64用十进制字符串，RPC仍使用原i64。
package platform

import (
	"bytes"
	"encoding/json"
	"errors"
	"strconv"
)

const maxSafeJSONInteger int64 = 9007199254740991

type httpI64 int64

// UnmarshalJSON 拒绝客户端已经可能舍入的数字，只允许精确十进制值转换到i64。
func (value *httpI64) UnmarshalJSON(raw []byte) error {
	text := string(raw)
	quoted := len(raw) > 0 && raw[0] == '"'
	if quoted {
		if e := json.Unmarshal(raw, &text); e != nil {
			return e
		}
	}
	n, e := strconv.ParseInt(text, 10, 64)
	// 关键：JSON number超过2^53-1无法证明浏览器提交的是原值，必须要求字符串，不能悄悄舍入。
	if e != nil || strconv.FormatInt(n, 10) != text || (!quoted && (n > maxSafeJSONInteger || n < -maxSafeJSONInteger)) {
		return errors.New("无效整数")
	}
	*value = httpI64(n)
	return nil
}

// safeHTTPJSON 从原始JSON数字词法投影，不先经float64，保留大i64的全部十进制位。
func safeHTTPJSON(value any) ([]byte, error) {
	raw, e := json.Marshal(value)
	if e != nil {
		return nil, e
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var document any
	if e = decoder.Decode(&document); e != nil {
		return nil, e
	}
	var project func(any) any
	project = func(v any) any {
		switch x := v.(type) {
		case json.Number:
			if n, e := strconv.ParseInt(string(x), 10, 64); e == nil && (n > maxSafeJSONInteger || n < -maxSafeJSONInteger) {
				return string(x)
			}
		case map[string]any:
			for k, item := range x {
				x[k] = project(item)
			}
		case []any:
			for i, item := range x {
				x[i] = project(item)
			}
		}
		return v
	}
	return json.Marshal(project(document))
}
