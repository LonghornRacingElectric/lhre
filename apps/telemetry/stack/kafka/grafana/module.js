System.register(['@grafana/data', '@grafana/runtime', '@grafana/ui', 'react', 'rxjs', 'rxjs/operators'], function (exports) {
  'use strict';
  var data, runtime, ui, React, rx, operators;
  return {
    setters: [function (m) { data = m; }, function (m) { runtime = m; }, function (m) { ui = m; }, function (m) { React = m; }, function (m) { rx = m; }, function (m) { operators = m; }],
    execute: function () {
      var h = React.createElement;
      class DataSource extends runtime.DataSourceWithBackend {
        getDefaultQuery() { return { signals: [], agg: 'mean', bucket_ms: 0, alias: '', live: false }; }
        filterQuery(q) { return !q.hide && ((q.signals || []).length > 0 || (q.pools || []).length > 0); }
        applyTemplateVariables(q, vars) {
          var expand = value => runtime.getTemplateSrv().replace(value, vars);
          var expandList = values => values.flatMap(value => runtime.getTemplateSrv().replace(value, vars, 'csv').split(',').filter(Boolean));
          return Object.assign({}, q, { signals: expandList(q.signals || []), pools: expandList(q.pools || []), alias: expand(q.alias || '') });
        }
        query(request) {
          var targets = request.targets.filter(q => this.filterQuery(q)).map(q => this.applyTemplateVariables(q, request.scopedVars));
          var history = targets.filter(q => !q.live);
          var streams = history.length ? [super.query(Object.assign({}, request, { targets: history }))] : [];
          targets.filter(q => q.live).forEach(q => {
            var maxPoints = Math.min(request.maxDataPoints || 1000, 10000);
            var body = { car: 'artemis', signals: q.signals || [], pools: q.pools || [], agg: q.agg || 'mean', bucket_ms: q.bucket_ms || 0, max_points: maxPoints, page_size: 10000, t0: new Date(request.range.from.valueOf()).toISOString(), t1: 'live', alias: q.alias || '', window_ms: request.range.to.valueOf() - request.range.from.valueOf() };
            if (body.agg === 'raw') { delete body.max_points; delete body.bucket_ms; }
            // A stable path isolates queries with different signals or buckets.
            var encoded = JSON.stringify(body), hash = 2166136261;
            for (var i = 0; i < encoded.length; i++) { hash = Math.imul(hash ^ encoded.charCodeAt(i), 16777619); }
            streams.push(runtime.getGrafanaLiveSrv().getDataStream({ addr: { scope: data.LiveChannelScope.DataSource, namespace: this.uid, path: 'query/' + (hash >>> 0).toString(16), data: body }, buffer: { maxLength: maxPoints, action: runtime.StreamingFrameAction.Replace } }).pipe(operators.map(response => Object.assign({}, response, { data: (response.data || []).map(frame => data.toDataFrame({ name: frame.name, refId: q.refId, meta: frame.meta, fields: frame.fields.map(field => Object.assign({}, field, { values: Array.from(field.values) })) })) }))));
          });
          return streams.length ? rx.merge.apply(null, streams) : rx.of({ data: [] });
        }
        async metricFindQuery() {
          var catalog = await this.getResource('signals');
          return catalog.signals.map(s => ({ text: s.signal, value: s.signal }));
        }
      }
      function QueryEditor(props) {
        var state = React.useState([]), signals = state[0], setSignals = state[1];
        var failure = React.useState(''), error = failure[0], setError = failure[1];
        React.useEffect(() => { var active = true; props.datasource.getResource('signals').then(c => { if (active) { setSignals(c.signals || []); } }).catch(e => { if (active) { setError(String(e)); } }); return () => { active = false; }; }, [props.datasource]);
        var q = props.query;
        function change(values) { props.onChange(Object.assign({}, q, values)); props.onRunQuery(); }
        var chosen = signals.filter(s => (q.signals || []).includes(s.signal));
        var funcs = ['mean', 'min', 'max', 'last', 'raw'].filter(agg => chosen.every(s => (s.aggregates || []).includes(agg)));
        return h('div', { style: { display: 'grid', gap: '8px', maxWidth: '960px' } },
          error ? h('div', { role: 'alert' }, error) : null,
          h(ui.InlineField, { label: 'Signals', grow: true }, h(ui.MultiSelect, { options: signals.map(s => ({ label: s.signal, value: s.signal, description: [s.unit, s.pool].filter(Boolean).join(' · ') })), value: q.signals || [], placeholder: 'Search Artemis signals', onChange: values => change({ signals: values.map(v => v.value) }) })),
          h(ui.InlineField, { label: 'Function' }, h(ui.Select, { options: funcs.map(v => ({ label: v, value: v })), value: q.agg || 'mean', onChange: v => change({ agg: v.value }) })),
          h(ui.InlineField, { label: 'Bucket (ms)', tooltip: '0 selects a bucket from panel width and native signal frequency.' }, h(ui.Input, { type: 'number', min: 0, value: q.bucket_ms || 0, onChange: e => props.onChange(Object.assign({}, q, { bucket_ms: Number(e.target.value) })), onBlur: props.onRunQuery })),
          h(ui.InlineField, { label: 'Alias', tooltip: 'Use {{signal}} for the qualified channel name.' }, h(ui.Input, { value: q.alias || '', onChange: e => props.onChange(Object.assign({}, q, { alias: e.target.value })), onBlur: props.onRunQuery })),
          h(ui.InlineField, { label: 'Live' }, h(ui.Switch, { value: !!q.live, onChange: e => change({ live: e.target.checked }) }))
        );
      }
      function ConfigEditor(props) {
        var options = props.options;
        return h('div', null,
          h(ui.InlineField, { label: 'Gateway URL', labelWidth: 20 }, h(ui.Input, { width: 60, value: options.jsonData.url || '', placeholder: 'http://query_gateway:8090', onChange: e => props.onOptionsChange(Object.assign({}, options, { jsonData: Object.assign({}, options.jsonData, { url: e.target.value }) })) })),
          h(ui.InlineField, { label: 'API token', labelWidth: 20 }, h(ui.SecretInput, { width: 60, isConfigured: !!(options.secureJsonFields || {}).apiToken, value: (options.secureJsonData || {}).apiToken || '', onChange: e => props.onOptionsChange(Object.assign({}, options, { secureJsonData: { apiToken: e.target.value } })), onReset: () => props.onOptionsChange(Object.assign({}, options, { secureJsonFields: { apiToken: false }, secureJsonData: { apiToken: '' } })) }))
        );
      }
      exports('plugin', new data.DataSourcePlugin(DataSource).setQueryEditor(QueryEditor).setConfigEditor(ConfigEditor));
    }
  };
});
