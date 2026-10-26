// 历史创建证据补齐与管理权限确认分离；权限决定不能伪装成创建时角色。
package control

import (
	"database/sql"
	"encoding/json"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
)

// historicalManagementSchema 追加不可变确认记录，保留每次确认人、时间及允许结果。
var historicalManagementSchema = []string{
	`CREATE TABLE IF NOT EXISTS instance_management_confirmations(sequence INTEGER PRIMARY KEY AUTOINCREMENT,instance_id TEXT NOT NULL REFERENCES instances(id),actor_id TEXT NOT NULL REFERENCES accounts(id),confirmed_at TEXT NOT NULL,allow_cluster_managers INTEGER NOT NULL CHECK(allow_cluster_managers IN (0,1)))`,
	`CREATE INDEX IF NOT EXISTS instance_management_latest ON instance_management_confirmations(instance_id,sequence DESC)`,
}

// migrateHistoricalCreators 以稳定实例 ID 关联唯一创建证据，不读取账号当前角色或集群 owner。
func migrateHistoricalCreators(tx *sql.Tx) error {
	type evidence struct {
		actor, target string
		result        rpc.ObservationInstance
		valid         bool
	}
	records := map[string][]evidence{}
	rows, err := tx.Query(`SELECT c.actor_id,c.target_id,c.result_json,EXISTS(SELECT 1 FROM accounts a WHERE a.id=c.actor_id) FROM command_results c WHERE c.action='CREATE_INSTANCE'`)
	if err != nil {
		return err
	}
	for rows.Next() {
		var item evidence
		var raw []byte
		if err = rows.Scan(&item.actor, &item.target, &raw, &item.valid); err != nil {
			rows.Close()
			return err
		}
		if json.Unmarshal(raw, &item.result) != nil || item.result.Id == "" {
			continue
		}
		records[item.result.Id] = append(records[item.result.Id], item)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for id, items := range records {
		// 关键：重复或矛盾记录均不猜测创建者；只验证不可变 cluster/component，草稿在首次发布前可合法改名或迁移 namespace。
		if len(items) != 1 || !items[0].valid {
			continue
		}
		item := items[0]
		i := item.result
		if i.ReadOnly || i.Ownership != "managed" || item.actor == "" || item.target != i.ClusterId {
			continue
		}
		_, err = tx.Exec(`UPDATE instances SET creator_user_id=? WHERE id=? AND cluster_id=? AND component=? AND creator_user_id='' AND creator_role=''`, item.actor, id, item.target, i.Component)
		if err != nil {
			return err
		}
	}
	return nil
}
