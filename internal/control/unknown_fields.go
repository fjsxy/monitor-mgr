package control

import (
	"reflect"
	"strconv"
	"strings"
)

// 未知字段只报告所在类型化字段路径，不读取或回显未知字段的名称和值。
func rejectUnknownFields(request any, path string) error {
	return walkUnknownFields(reflect.ValueOf(request), path, 0)
}

// 递归检查类型化配置及其容器；只走导出的字段，未知 wire 数据始终由生成的 carrier 判断。
func walkUnknownFields(value reflect.Value, path string, depth int) error {
	if !value.IsValid() {
		return nil
	}
	if depth > 64 {
		return fieldError(path, "请求嵌套过深")
	}
	switch value.Kind() {
	case reflect.Pointer, reflect.Interface, reflect.Map, reflect.Slice:
		if value.IsNil() {
			return nil
		}
	}
	// Interface 本身非 nil 时仍可能包含 typed nil；先解包再调用生成的 carrier 方法。
	if value.Kind() == reflect.Interface {
		return walkUnknownFields(value.Elem(), path, depth+1)
	}
	if value.CanInterface() {
		if carrier, ok := value.Interface().(interface{ CarryingUnknownFields() bool }); ok && carrier.CarryingUnknownFields() {
			return fieldError(path, "包含不支持的 Thrift 字段")
		}
	}
	switch value.Kind() {
	case reflect.Pointer:
		return walkUnknownFields(value.Elem(), path, depth+1)
	case reflect.Struct:
		for i := 0; i < value.NumField(); i++ {
			field := value.Type().Field(i)
			// 关键：不反射私有 carrier 或忽略字段，更不能把敏感原值放入错误路径。
			if field.PkgPath != "" {
				continue
			}
			name := strings.Split(field.Tag.Get("json"), ",")[0]
			if name == "-" {
				continue
			}
			if name == "" {
				name = field.Name
			}
			if err := walkUnknownFields(value.Field(i), path+"."+name, depth+1); err != nil {
				return err
			}
		}
	case reflect.Slice, reflect.Array:
		for i := 0; i < value.Len(); i++ {
			if err := walkUnknownFields(value.Index(i), path+"["+strconv.Itoa(i)+"]", depth+1); err != nil {
				return err
			}
		}
	case reflect.Map:
		items := value.MapRange()
		for items.Next() {
			// Map key 可能来自用户输入，路径只使用通配符，防止凭据或自定义键泄漏。
			for _, item := range []reflect.Value{items.Key(), items.Value()} {
				if err := walkUnknownFields(item, path+"[*]", depth+1); err != nil {
					return err
				}
			}
		}
	}
	return nil
}
