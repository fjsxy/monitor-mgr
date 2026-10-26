-- 升级前一个 component 已成功、ServiceMonitor 尚未结束的原任务。
INSERT INTO version_resources VALUES($version,'component',0,'/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics','/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/lifecycle',$document);
INSERT INTO version_resources VALUES($version,'companion',1,'/apis/monitoring.coreos.com/v1/namespaces/old-monitoring/servicemonitors','/apis/monitoring.coreos.com/v1/namespaces/old-monitoring/servicemonitors/old-sm','{"apiVersion":"monitoring.coreos.com/v1","kind":"ServiceMonitor","metadata":{"name":"old-sm","namespace":"old-monitoring","labels":{"monitor-mgr.io/user-id":"legacy-user"}},"spec":{"selector":{"matchLabels":{"legacy":"frozen"}},"endpoints":[{"port":"metrics"}]}}');
INSERT INTO operation_resources SELECT $operation,resource_key,position,collection_path,object_path,document,CASE WHEN resource_key='component' THEN 'created-uid' ELSE $uid END FROM version_resources;
INSERT INTO resource_progress VALUES($attempt,'component','created-uid',1,'SUCCEEDED','原组件已确认成功','2026-10-22T00:00:00Z');
INSERT INTO resource_progress VALUES($attempt,'companion',$uid,$possiblySent,$state,'旧 SM 原执行结果','2026-10-22T00:00:00Z');
INSERT INTO instance_resource_identities VALUES($instance,'component','created-uid');
UPDATE operations SET state=$operationState,message='旧双 CR 原操作' WHERE id=$operation;
UPDATE execution_attempts SET state=$operationState,possibly_sent=1 WHERE id=$attempt;
UPDATE instances SET current_version_id='' WHERE id=$instance;
INSERT INTO instance_protections VALUES($instance,$operation);
