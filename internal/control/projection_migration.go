// 历史读模型升级：不访问集群，不改变原预览基线，仅把原规范化字节替换为安全指纹。
package control

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"gopkg.in/yaml.v3"
)

func migrateTargetProjections(tx *sql.Tx, key []byte) error {
	rows, e := tx.Query(`SELECT p.id,p.target_spec,i.id,i.cluster_id,i.component,i.name,i.target_namespace,p.uid FROM publication_previews p JOIN instances i ON i.id=p.instance_id`)
	if e != nil {
		return e
	}
	type preview struct {
		id, raw string
		i       rpc.ObservationInstance
	}
	items := []preview{}
	for rows.Next() {
		var p preview
		if e = rows.Scan(&p.id, &p.raw, &p.i.Id, &p.i.ClusterId, &p.i.Component, &p.i.Name, &p.i.TargetNamespace, &p.i.Uid); e != nil {
			rows.Close()
			return e
		}
		items = append(items, p)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	for _, p := range items {
		if strings.HasPrefix(p.raw, "hmac:v1:") {
			continue
		}
		var value any
		if json.Unmarshal([]byte(p.raw), &value) != nil {
			return fmt.Errorf("原预览规范化格式无效")
		}
		if _, e = tx.Exec(`UPDATE publication_previews SET target_spec=? WHERE id=?`, targetFingerprint(key, &p.i, p.raw), p.id); e != nil {
			return e
		}
	}
	// 关键：历史CR/YAML和已派生Runtime.Error共同修复，防止离线首次响应绕过新来源投影。
	for _, field := range []struct{ table, id, col string }{
		{"instance_observations", "instance_id", "cr"},
		{"discovered_instances", "id", "document"},
		{"instance_observations", "instance_id", "runtime"},
	} {
		table, id, col := field.table, field.id, field.col
		rows, e = tx.Query(`SELECT ` + id + `,` + col + ` FROM ` + table)
		if e != nil {
			return e
		}
		type record struct {
			id  string
			raw []byte
		}
		records := []record{}
		for rows.Next() {
			var r record
			if e = rows.Scan(&r.id, &r.raw); e != nil {
				rows.Close()
				return e
			}
			records = append(records, r)
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return e
		}
		for _, r := range records {
			var doc map[string]any
			if json.Unmarshal(r.raw, &doc) != nil {
				return fmt.Errorf("历史读模型格式无效")
			}
			if col == "runtime" {
				// 复用状态诊断规则；旧savedObservation包装和普通故障原因均保留。
				sanitizeStatus(doc)
			} else if table == "discovered_instances" {
				if text, ok := doc["yaml"].(string); ok {
					var cr map[string]any
					if yaml.Unmarshal([]byte(text), &cr) != nil {
						return fmt.Errorf("历史YAML格式无效")
					}
					doc["yaml"] = yamlText(safeTargetDocument(cr))
				}
				sanitizeStatus(doc["runtime"])
			} else {
				doc = safeTargetDocument(doc)
			}
			raw, _ := json.Marshal(doc)
			if _, e = tx.Exec(`UPDATE `+table+` SET `+col+`=? WHERE `+id+`=?`, raw, r.id); e != nil {
				return e
			}
		}
	}
	return nil
}
