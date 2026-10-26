// 后台CR同步复用应用worker生命周期、当前凭据和公共并发预算，不依赖浏览器或用户Session。
package control

import (
	"context"
	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"time"
)

// launchObservations 仅在workerLaunch锁内安排任务，退出开始后不增加未登记goroutine。
func (s *Store) launchObservations(life context.Context) {
	rows, e := s.DB.QueryContext(life, `SELECT i.id,i.cluster_id FROM instances i JOIN clusters c ON c.id=i.cluster_id WHERE c.state='ACTIVE' AND i.uid<>'' AND i.lifecycle<>'DELETED'`)
	if e != nil {
		return
	}
	type instance struct{ id, cluster string }
	items := []instance{}
	for rows.Next() {
		var i instance
		if rows.Scan(&i.id, &i.cluster) == nil {
			items = append(items, i)
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return
	}
	clusters := map[string]bool{}
	for _, i := range items {
		clusters[i.cluster] = true
	}
	// 没有平台实例的已注册集群也必须周期发现只读外部CR。
	clusterRows, err := s.DB.QueryContext(life, `SELECT id FROM clusters WHERE state='ACTIVE'`)
	if err == nil {
		for clusterRows.Next() {
			var id string
			if clusterRows.Scan(&id) == nil {
				clusters[id] = true
			}
		}
		clusterRows.Close()
	}
	for id := range clusters {
		s.launchInventory(life, id)
	}
	for _, i := range items {
		if _, busy := s.observationPolls.LoadOrStore(i.id, true); busy {
			continue
		}
		s.publicationWG.Add(1)
		go func(i instance) {
			defer s.publicationWG.Done()
			defer s.observationPolls.Delete(i.id)
			ctx, cancel := context.WithTimeout(life, 8*time.Second)
			defer cancel()
			instance, err := instanceByID(ctx, s.DB, i.id)
			if err != nil {
				return
			}
			cluster, err := clusterByID(ctx, s.DB, i.cluster)
			if err != nil {
				return
			}
			// 关键：身份预读本身也可能失败；在网络前固定范围，不能把迟到失败标到新凭据修订。
			failedScope := context.WithValue(ctx, observationScopeKey{}, observationScope{instance.Id, instance.Uid, instance.ClusterId, cluster.Uid, instance.Name, instance.TargetNamespace, cluster.CredentialRevision})
			target, _, err := s.targetForRecovery(ctx, i.cluster)
			if err != nil {
				s.saveSourceObservation(failedScope, instance, "CR", nil, err)
				if instance.Component == KSMComponent {
					s.saveSourceObservation(failedScope, instance, companionSource, nil, err)
				}
				return
			}
			defer target.Close()
			s.refreshObserved(ctx, i.id, target)
		}(i)
	}
}

// launchInventory 与单实例GET各自隔离，某组件分页失败不会阻断其他实例或集群。
func (s *Store) launchInventory(life context.Context, cluster string) {
	key := "inventory:" + cluster
	if _, busy := s.observationPolls.LoadOrStore(key, true); busy {
		return
	}
	s.publicationWG.Add(1)
	go func() {
		defer s.publicationWG.Done()
		defer s.observationPolls.Delete(key)
		ctx, cancel := context.WithTimeout(life, 8*time.Second)
		defer cancel()
		original, err := clusterByID(ctx, s.DB, cluster)
		if err != nil {
			return
		}
		scope := discoveryScope{original.Uid, original.CredentialRevision}
		target, _, err := s.targetForRecovery(ctx, cluster)
		if err != nil {
			for _, component := range []string{KSMComponent, AgentComponent} {
				s.persistDiscovery(ctx, cluster, component, nil, err, scope)
			}
			return
		}
		defer target.Close()
		rows, err := s.DB.QueryContext(ctx, `SELECT `+instanceColumns+` FROM instances WHERE cluster_id=?`, cluster)
		if err != nil {
			return
		}
		managed := []*rpc.ObservationInstance{}
		for rows.Next() {
			i, e := scanInstance(rows)
			if e != nil {
				rows.Close()
				return
			}
			managed = append(managed, i)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return
		}
		s.discoverTarget(ctx, cluster, managed, target)
	}()
}
