(() => {
  'use strict';

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
  const pad = (n) => String(n).padStart(2, '0');
  const clock = (date) => `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const fullTime = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${clock(date)}`;
  const dayKey = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const relTime = (date) => {
    const now = new Date();
    const today = dayKey(now);
    const target = dayKey(date);
    if (target === today) return `今天 ${clock(date)}`;
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (target === dayKey(yesterday)) return `昨天 ${clock(date)}`;
    return `${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${clock(date)}`;
  };
  const dateStamp = (date) => `${String(date.getFullYear()).slice(2)}${pad(date.getMonth() + 1)}${pad(date.getDate())}`;

  let session;
  try {
    session = JSON.parse(sessionStorage.getItem('medguard.demo.session') || 'null');
  } catch (error) {
    session = null;
  }

  const roleMeta = {
    staff: {
      avatar: '医', badge: '只读', token: 'staff',
      name: '医生001 · 心内科',
      scopeName: '所辖患者群体',
      scopeDesc: '汇总与去标识明细。不包含全量身份信息，不开放批量导出。',
      meta: '心内科 · staff',
      queryHint: '例如：统计各科室接诊量',
    },
    patient: {
      avatar: '患', badge: '仅本人', token: 'patient',
      name: '患者001',
      scopeName: '本人病历',
      scopeDesc: '仅可读取当前令牌绑定的本人记录，不包含其他患者的汇总与明细。',
      meta: '本人 · patient',
      queryHint: '例如：我上次的血糖是多少',
    },
    admin: {
      avatar: '管', badge: '管理', token: 'admin',
      name: '信息科管理员',
      scopeName: '全院数据与安全策略',
      scopeDesc: '查看全院汇总与审计证据。查询本身仍受准入与审计策略约束。',
      meta: '信息科 · admin',
      queryHint: '例如：按诊断结果分类统计患者数',
    },
  };

  if (!session || !roleMeta[session.role]) {
    session = { account: 'doctor', displayName: '医生001 · 心内科', role: 'staff', scope: '所辖患者群体' };
  }
  const role = session.role;
  const meta = roleMeta[role];
  session.displayName = session.displayName || meta.name;
  const isPatient = role === 'patient';

  const viewMeta = {
    ask: { title: '可信问答', docTitle: '可信问答 · MedGuard 医盾' },
    doctor: { title: '智慧医生', docTitle: '智慧医生 · MedGuard 医盾' },
    reports: { title: '安全报告', docTitle: '安全报告 · MedGuard 医盾' },
  };
  const state = { view: null, reportId: null, queryBusy: false, chatBusy: false };
  const reportStore = [];
  let reportSequence = 0;

  const appMain = $('appMain');
  const appContent = $('workspaceMain');
  const viewWash = $('viewWash');
  const queryInput = $('queryInput');
  const queryForm = $('queryForm');
  const querySubmit = $('querySubmit');
  const querySubmitLabel = $('querySubmitLabel');
  const queryHelp = $('queryHelp');
  const resultShell = $('resultShell');
  const resultEmpty = $('resultEmpty');
  const resultPanel = $('resultPanel');
  const presetList = $('presetList');
  const reportList = $('reportList');
  const reportDetail = $('reportDetail');
  const reportCount = $('reportCount');
  const reportListMeta = $('reportListMeta');
  const chatScroll = $('chatScroll');
  const chatForm = $('chatForm');
  const chatInput = $('chatInput');
  const chatSend = $('chatSend');
  const chatChips = $('chatChips');
  const toast = $('toast');

  /* ── 身份与范围 ─────────────────────────────────── */
  function paintIdentity() {
    const shortName = session.displayName;
    $('sideAvatar').textContent = meta.avatar;
    $('topAvatar').textContent = meta.avatar;
    $('sideUserName').textContent = shortName;
    $('topUserName').textContent = shortName;
    $('topUserMeta').textContent = '演示账号';
    $('sideUserMeta').textContent = meta.scopeName;
    $('scopeName').textContent = meta.scopeName;
    $('scopeDesc').textContent = meta.scopeDesc;
    $('scopeBadge').textContent = meta.badge;
    $('askScope').textContent = meta.scopeName;
    $('chatSubject').textContent = isPatient ? '患者001 · 本人' : '患者001 · 演示';
    queryInput.placeholder = meta.queryHint;
  }

  const presetMap = {
    staff: [
      '统计各科室接诊量',
      '糖尿病患者产生了多少费用',
      '按诊断结果分类统计患者数',
      '导出患者基本信息核对表',
      '心内科有哪些医生',
    ],
    patient: [
      '我上次的血糖是多少',
      '医生给我开的药怎么吃',
      '我的影像报告怎么说',
      '心内科有哪些医生',
      '得这个病的有多少人',
    ],
    admin: [
      '统计各科室接诊量',
      '糖尿病患者产生了多少费用',
      '按诊断结果分类统计患者数',
      '导出患者基本信息核对表',
    ],
  };

  function paintPresets() {
    presetList.innerHTML = '';
    presetMap[role].forEach((question) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'preset-chip';
      button.textContent = question;
      button.addEventListener('click', () => {
        if (state.queryBusy) return;
        queryInput.value = question;
        autoResize(queryInput);
        queryForm.requestSubmit();
      });
      presetList.append(button);
    });
  }

  /* ── 查询结果模型 ───────────────────────────────── */
  const planMap = {
    dept: [
      { title: '按科室聚合就诊人次', detail: '只读取与接诊量相关的就诊记录' },
      { title: '输出科室接诊量', detail: '仅返回科室级计数，不返回患者标识' },
    ],
    cost: [
      { title: '关联就诊诊断与费用账单', detail: '诊断口径限定为含“糖尿病”的就诊' },
      { title: '按诊断汇总费用', detail: '账单去重后求和，不返回个人明细' },
    ],
    diagnosis: [
      { title: '按诊断分类统计患者数', detail: '同一患者的多条诊断只计一次' },
      { title: '合并小样本类别', detail: '患者数不足 5 人的类别不单独展示' },
    ],
    export: [
      { title: '请求读取患者身份信息', detail: '计划包含姓名与证件号等直接标识' },
    ],
    lab: [
      { title: '按当前令牌限定为本人记录', detail: '将“我”绑定到令牌主体' },
      { title: '筛选检验项目“血糖”', detail: '仅返回本人检验结果' },
    ],
    medication: [
      { title: '按当前令牌限定为本人记录', detail: '将“我”绑定到令牌主体' },
      { title: '筛选用药记录', detail: '仅返回药品、用量、频次与给药途径' },
    ],
    imaging: [
      { title: '按当前令牌限定为本人记录', detail: '将“我”绑定到令牌主体' },
      { title: '筛选影像结论', detail: '仅返回检查部位与结论，不生成诊断' },
    ],
    doctors: [
      { title: '筛选心内科在册医生', detail: '返回姓名、职称与专长' },
      { title: '去除非必要联系方式', detail: '不返回电话等直接联系信息' },
    ],
    scope: [
      { title: '解析查询范围', detail: '请求指向群体统计，而非本人记录' },
      { title: '校验令牌边界', detail: '当前令牌仅授权本人记录' },
    ],
    personal: [
      { title: '解析查询主体', detail: '“我的”在当前身份下没有可绑定的患者主体' },
      { title: '校验令牌边界', detail: '医生身份不提供个人病历上下文' },
    ],
    unclear: [
      { title: '解析问题', detail: '未找到已审核且范围明确的可执行查询' },
    ],
  };

  const rewriteMap = {
    dept: ['将“接诊量”限定为已登记就诊记录', '聚合结果去除患者编号与姓名', '仅返回科室级计数'],
    cost: ['将“糖尿病”限定为诊断名称含“糖尿病”的就诊', '账单金额去重后汇总', '不返回个人账单明细'],
    diagnosis: ['同一患者的多条诊断只计一次', '患者数不足 5 人的类别合并保护', '不返回患者编号与身份信息'],
    lab: ['将“我”绑定到当前令牌主体', '仅返回本人检验结果', '不回传其他患者记录'],
    medication: ['将“我”绑定到当前令牌主体', '仅返回处方中写明的用法', '不生成剂量调整建议'],
    imaging: ['将“我”绑定到当前令牌主体', '仅返回影像结论', '不把影像结论解释为诊断'],
    doctors: ['限定科室为心内科', '去除电话等非必要字段', '仅返回公开执业信息'],
    export: [],
    scope: ['不扩展检索范围', '不执行群体统计'],
    personal: ['不猜测患者主体', '不访问其他患者记录'],
    unclear: [],
  };

  function makeOutcome(kind, question, extra) {
    const outcome = {
      kind,
      question,
      status: 'ok',
      statusText: '已执行',
      executed: true,
      auditMs: 1 + Math.floor(Math.random() * 9),
      execMs: 3 + Math.floor(Math.random() * 62),
      plan: planMap[kind] || planMap.unclear,
      rewrite: rewriteMap[kind] || [],
      body: '',
      eventReason: '',
      id: '',
      time: new Date(),
    };
    Object.assign(outcome, extra || {});
    outcome.events = makeEvents(outcome);
    return outcome;
  }

  function makeEvents(outcome) {
    if (outcome.status === 'denied') {
      return [
        { text: '令牌校验通过', time: '0 ms', state: 'done' },
        { text: outcome.eventReason || '一层准入拒绝：超出授权范围', time: `${outcome.auditMs} ms`, state: 'denied' },
        { text: '执行已终止 · 医院主库未访问', time: '—', state: 'denied' },
      ];
    }
    if (!outcome.executed) {
      return [
        { text: '令牌校验通过', time: '0 ms', state: 'done' },
        { text: outcome.eventReason || '未生成可执行计划', time: `${outcome.auditMs} ms`, state: 'done' },
        { text: '执行未启动 · 医院主库未访问', time: '—', state: 'done' },
      ];
    }
    return [
      { text: '令牌校验通过', time: '0 ms', state: 'done' },
      { text: '一层准入通过', time: '1 ms', state: 'done' },
      { text: '二层审计通过', time: `${outcome.auditMs} ms`, state: 'done' },
      { text: '医院主库只读执行', time: `${outcome.execMs} ms`, state: 'done' },
      { text: '结果按范围返回', time: `${outcome.execMs + 2} ms`, state: 'done' },
    ];
  }

  const sourceTags = {
    dept: ['医院主库', '聚合视图', '已去标识'],
    cost: ['医院主库', '费用账单', '聚合结果'],
    diagnosis: ['医院主库', '诊断记录', '小样本保护'],
    lab: ['医院主库', '本人记录', '检验'],
    medication: ['医院主库', '本人记录', '处方'],
    imaging: ['医院主库', '本人记录', '影像'],
    doctors: ['医院审核知识库', '公开执业信息'],
  };

  function sourceLine(kind) {
    return `<div class="source-line">${(sourceTags[kind] || []).map((tag) => `<span>${tag}</span>`).join('')}</div>`;
  }

  function deniedOutcome(kind, question, options) {
    return makeOutcome(kind, question, {
      status: 'denied',
      statusText: options.statusText || '一层拒绝',
      executed: false,
      eventReason: options.eventReason,
      body: `
        <div class="denied-box">
          <div class="denied-top">
            <span class="denied-sign" aria-hidden="true">止</span>
            <div><h4>${options.title}</h4><p>${options.copy}</p></div>
          </div>
          <ul class="denied-list">
            ${options.items.map((item) => `<li>${item[0]}<span>${item[1]}</span></li>`).join('')}
          </ul>
          <div class="denied-foot"><span>未访问主库</span><span>未生成文件</span><span>已写入审计</span></div>
        </div>`,
    });
  }

  function buildOutcome(question) {
    const compact = question.replace(/\s+/g, '');
    const has = (pattern) => pattern.test(compact);
    let kind = 'unknown';
    if (has(/导出|核对表|名单|批量下载/)) kind = 'export';
    else if (has(/血糖|糖化血红蛋白|化验|检验结果/)) kind = 'lab';
    else if (has(/怎么吃|用药|服药|药量|剂量|头孢/)) kind = 'medication';
    else if (has(/影像|核磁|MRI|CT|拍片|报告怎么看/)) kind = 'imaging';
    else if (has(/心内科.*医生|医生.*心内科|心内科.*挂号|挂号.*心内科/)) kind = 'doctors';
    else if (has(/糖尿病.*费用|费用.*糖尿病/)) kind = 'cost';
    else if (has(/统计.*科室|科室.*接诊|接诊量/)) kind = 'dept';
    else if (has(/诊断.*分类|分类.*患者|各诊断/)) kind = 'diagnosis';
    else if (has(/得这个病|有多少人|患病人数/)) kind = 'others';

    if (kind === 'export') {
      return deniedOutcome('export', question, {
        title: '批量导出被一层准入拦截',
        copy: '计划中包含患者姓名、证件号等直接身份标识，超出本次查询的最小必要范围。请求在执行前终止，没有生成文件。',
        items: [
          ['患者姓名', '直接标识'],
          ['身份证件号', '直接标识'],
          ['批量患者范围', '超出最小必要'],
        ],
        eventReason: '一层准入拒绝：含直接身份标识与批量范围',
      });
    }

    if (isPatient && ['dept', 'cost', 'diagnosis', 'others'].includes(kind)) {
      return deniedOutcome('scope', question, {
        title: '群体统计超出本人授权',
        copy: '当前令牌只允许读取本人诊疗记录，不包含其他患者的汇总或明细。系统没有尝试扩大检索范围。',
        items: [
          ['其他患者的记录', '无授权'],
          ['群体统计结果', '无授权'],
          ['跨患者明细', '无授权'],
        ],
        statusText: '范围拒绝',
        eventReason: '一层准入拒绝：令牌仅授权本人记录',
      });
    }

    if (!isPatient && ['lab', 'medication', 'imaging'].includes(kind)) {
      return deniedOutcome('personal', question, {
        title: '当前身份没有个人病历上下文',
        copy: '医生身份可用于群体与所辖患者范围，但系统不能把“我”解析为某位患者。请使用患者演示身份，或在正式流程中先指定获授权的患者。',
        items: [
          ['个人检验结果', '缺少患者主体'],
          ['个人用药记录', '缺少患者主体'],
          ['个人影像报告', '缺少患者主体'],
        ],
        statusText: '缺少主体',
        eventReason: '一层准入拒绝：未提供获授权的患者主体',
      });
    }

    if (kind === 'others') {
      const suggestions = isPatient
        ? ['我上次的血糖是多少', '我的影像报告怎么说']
        : ['按诊断结果分类统计患者数', '糖尿病患者产生了多少费用'];
      return makeOutcome('unclear', question, {
        status: 'warn',
        statusText: '需补充范围',
        executed: false,
        eventReason: '统计口径不完整 · 未生成执行计划',
        body: `
          <div class="warn-box">
            <h4>“这个病”还没有指向明确疾病。</h4>
            <p>系统没有把模糊问题扩展成群体统计。即使补全疾病名称，患者数不足 5 人的细分仍可能暴露个体，当前防御也不覆盖小样本组合推断。</p>
            <ul>${suggestions.map((text) => `<li><button class="preset-chip" type="button" data-fill="${esc(text)}">${text}</button></li>`).join('')}</ul>
          </div>`,
      });
    }

    if (kind === 'unknown') {
      const suggestions = isPatient
        ? ['我上次的血糖是多少', '医生给我开的药怎么吃', '我的影像报告怎么说']
        : ['统计各科室接诊量', '糖尿病患者产生了多少费用', '按诊断结果分类统计患者数'];
      return makeOutcome('unclear', question, {
        status: 'warn',
        statusText: '未执行',
        executed: false,
        eventReason: '未命中已审核查询 · 未访问主库',
        body: `
          <div class="warn-box">
            <h4>这个问题还没有安全的执行路径。</h4>
            <p>演示控制台只运行已审核、范围明确的查询模板。没有命中时不会拼接相似查询，也不会读取医院主库。请换一种更具体的问法。</p>
            <ul>${suggestions.map((text) => `<li><button class="preset-chip" type="button" data-fill="${esc(text)}">${text}</button></li>`).join('')}</ul>
          </div>`,
      });
    }

    if (kind === 'dept') {
      const rows = [
        ['消化内科', 20], ['内分泌科', 20], ['神经内科', 19],
        ['骨科', 16], ['心内科', 16], ['呼吸科', 13],
      ];
      const total = rows.reduce((sum, row) => sum + row[1], 0);
      const max = Math.max(...rows.map((row) => row[1]));
      return makeOutcome(kind, question, {
        body: `
          <p class="body-lede">在授权范围内按科室聚合就诊人次，结果已去除患者标识。当前演示库共 ${total} 次就诊。</p>
          <div class="data-table-wrap">
            <table class="data-table">
              <thead><tr><th>科室</th><th class="num">接诊人次</th><th class="bar-cell">分布</th></tr></thead>
              <tbody>${rows.map(([name, count]) => `
                <tr><td class="emphasis">${name}</td><td class="num">${count}</td><td class="bar-cell"><span class="mini-bar"><i style="width:${Math.round(count / max * 100)}%"></i></span></td></tr>`).join('')}
              </tbody>
              <tfoot><tr><td class="emphasis">合计</td><td class="num">${total}</td><td class="bar-cell"></td></tr></tfoot>
            </table>
          </div>
          <p class="table-note">来源：医院主库 · 聚合视图 · 只读执行。演示库数据均为虚构测试数据。</p>
          ${sourceLine(kind)}`,
      });
    }

    if (kind === 'cost') {
      return makeOutcome(kind, question, {
        body: `
          <p class="body-lede">口径：就诊诊断名称含“糖尿病”的已结算账单金额，按账单去重后汇总。结果不含个人费用明细。</p>
          <div class="metric-hero">
            <div class="big-value">36,600.50<small>元</small></div>
            <div class="hero-side"><strong>8 位患者的糖尿病相关就诊</strong><span>费用记录 20 条 · 跨多次就诊与多个科室</span></div>
          </div>
          <div class="metric-split" style="margin-top:18px">
            <div><span>去重患者</span><strong>8 位</strong></div>
            <div><span>费用记录</span><strong>20 条</strong></div>
            <div><span>返回粒度</span><strong>聚合总计</strong></div>
          </div>
          <p class="table-note">来源：医院主库 · 费用账单与诊断记录 · 只读执行。演示数据，不代表真实费用水平。</p>
          ${sourceLine(kind)}`,
      });
    }

    if (kind === 'diagnosis') {
      const rows = [['2 型糖尿病', 8], ['胃炎', 7], ['高血压', 5], ['上呼吸道感染', 5]];
      return makeOutcome(kind, question, {
        status: 'warn',
        statusText: '部分保护',
        body: `
          <p class="body-lede">按诊断分类统计患者数，同一患者的多条诊断只计一次。患者数不足 5 人的类别不单独展示。</p>
          <div class="data-table-wrap">
            <table class="data-table">
              <thead><tr><th>诊断分类</th><th class="num">患者数</th><th class="bar-cell">相对规模</th></tr></thead>
              <tbody>${rows.map(([name, count], index) => `
                <tr><td class="emphasis">${name}</td><td class="num">${count}</td><td class="bar-cell"><span class="mini-bar"><i style="width:${[100, 88, 63, 63][index]}%"></i></span></td></tr>`).join('')}
              </tbody>
            </table>
          </div>
          <div class="small-sample">
            <strong>2 类诊断已合并保护</strong>
            <p>支气管炎（2 人）与腰椎间盘突出（1 人）不单独返回。当前防御不覆盖通过多组小样本组合推断个体的风险。</p>
          </div>
          <p class="table-note">患者可同时具有多个诊断，各行患者数不可相加。来源：医院主库 · 只读执行。</p>
          ${sourceLine(kind)}`,
      });
    }

    if (kind === 'lab') {
      return makeOutcome(kind, question, {
        body: `
          <div class="personal-result">
            <div class="personal-value"><span>血糖 / GLUCOSE</span><strong>6.3<small>mmol/L</small></strong><p>2025-04-05 · 心内科门诊检验 · 演示记录</p></div>
            <div>
              <div class="personal-lines">
                <div class="personal-line"><span>记录主体</span><strong>患者001（本人）</strong></div>
                <div class="personal-line"><span>项目</span><strong>血糖</strong></div>
                <div class="personal-line"><span>结果</span><strong>6.3 mmol/L</strong></div>
                <div class="personal-line"><span>参考说明</span><strong>演示记录未注明是否空腹</strong></div>
              </div>
              <p class="personal-disclaimer">是否空腹会影响判读。控制台只返回记录本身，不提供诊断；如需通俗解释，请使用智慧医生。</p>
            </div>
          </div>
          ${sourceLine(kind)}`,
      });
    }

    if (kind === 'medication') {
      return makeOutcome(kind, question, {
        body: `
          <div class="personal-result">
            <div class="personal-value"><span>头孢呋辛 / CEFUROXIME</span><strong>1片<small>每次</small></strong><p>每日三次 · 口服 · 2025-04-05 心内科门诊</p></div>
            <div>
              <div class="personal-lines">
                <div class="personal-line"><span>记录主体</span><strong>患者001（本人）</strong></div>
                <div class="personal-line"><span>单次用量</span><strong>1 片</strong></div>
                <div class="personal-line"><span>频次</span><strong>每日三次</strong></div>
                <div class="personal-line"><span>给药途径</span><strong>口服</strong></div>
              </div>
              <p class="personal-disclaimer">以上内容来自处方记录，不构成用药调整建议。具体疗程、过敏史与药物相互作用请向开方医生或药师确认。</p>
            </div>
          </div>
          ${sourceLine(kind)}`,
      });
    }

    if (kind === 'imaging') {
      return makeOutcome(kind, question, {
        body: `
          <div class="personal-result">
            <div class="personal-value"><span>腹部 MRI / IMAGING</span><strong class="text-value">未见明显异常</strong><p>2025-04-05 · 心内科门诊 · 测试数据</p></div>
            <div>
              <div class="personal-lines">
                <div class="personal-line"><span>记录主体</span><strong>患者001（本人）</strong></div>
                <div class="personal-line"><span>检查方式</span><strong>MRI</strong></div>
                <div class="personal-line"><span>检查部位</span><strong>腹部</strong></div>
                <div class="personal-line"><span>结论</span><strong>未见异常（测试数据）</strong></div>
              </div>
              <p class="personal-disclaimer">影像结论需结合症状、化验与医生查体。控制台不把“未见明显异常”解释为排除所有问题。</p>
            </div>
          </div>
          ${sourceLine(kind)}`,
      });
    }

    if (kind === 'doctors') {
      return makeOutcome(kind, question, {
        body: `
          <p class="body-lede">心内科在册医生，以下为演示数据中的公开执业信息，不含联系电话。</p>
          <div class="doctor-cards">
            <article class="doctor-card"><div class="doctor-name">医生001</div><div class="doctor-title">副主任医师</div><p>专长：心血管疾病</p></article>
            <article class="doctor-card"><div class="doctor-name">医生002</div><div class="doctor-title">主治医师</div><p>专长：冠心病介入</p></article>
          </div>
          <p class="table-note">出诊时间与号源以医院当日公示为准。来源：医院审核知识库 · 只读执行。</p>
          ${sourceLine(kind)}`,
      });
    }

    return makeOutcome('unclear', question, {
      status: 'warn',
      statusText: '未执行',
      executed: false,
      eventReason: '未命中已审核查询 · 未访问主库',
      body: `<div class="warn-box"><h4>暂时无法安全执行。</h4><p>请换一种更具体的问法。</p></div>`,
    });
  }

  /* ── 渲染查询结果 ───────────────────────────────── */
  function executionLabel(outcome) {
    return outcome.executed ? `主库只读 · ${outcome.execMs} ms` : '未启动';
  }

  function renderResult(outcome, example) {
    resultEmpty.hidden = true;
    resultPanel.hidden = false;
    resultPanel.innerHTML = `
      <div class="result-head">
        <div>
          <div class="result-kicker">
            <span>AUDIT / ${outcome.id || '本次查询'}</span><span class="dot"></span>
            <span>${example ? '最近一次查询' : '本次查询'}</span><span class="dot"></span>
            <span>${relTime(outcome.time)}</span>
          </div>
          <h3>${esc(outcome.question)}</h3>
          <p class="result-meta">审计层指标 · LLM 0 / 数据库 0 · 审计耗时 ${outcome.auditMs} ms · ${executionLabel(outcome)}</p>
        </div>
        <span class="result-status ${outcome.status}">${outcome.statusText}</span>
      </div>
      <div class="result-metrics">
        <div class="result-metric"><span>审计层 · LLM 调用</span><strong>0 <em>次</em></strong></div>
        <div class="result-metric"><span>审计层 · 数据库访问</span><strong>0 <em>次</em></strong></div>
        <div class="result-metric"><span>执行阶段</span><strong>${outcome.executed ? '主库只读' : '未启动'} <em>${outcome.executed ? '1 次' : '0 次'}</em></strong></div>
      </div>
      <div class="result-body">${outcome.body}</div>`;
  }

  function processingHTML(outcome) {
    const steps = [
      ['身份校验', 'TOKEN'],
      ['生成计划', 'PLAN'],
      ['一层准入', 'ADMISSION'],
      ['二层审计', 'AUDIT'],
      ['只读执行', 'EXECUTE'],
    ];
    return `
      <div class="processing" role="status" aria-label="正在审查查询">
        <div class="processing-ring" aria-hidden="true"></div>
        <div>
          <p class="eyebrow">QUERY REVIEW / 正在审查</p>
          <h3>先把问题放进安全边界。</h3>
          <p id="processNote">正在确认身份与数据范围……</p>
          <div class="audit-trail" id="auditTrail">
            ${steps.map(([label, code]) => `<div class="trail-step"><span>${label}</span><small>${code}</small></div>`).join('')}
          </div>
        </div>
      </div>`;
  }

  function runQuery(rawQuestion) {
    if (state.queryBusy) return;
    const question = String(rawQuestion || '').trim();
    if (!question) {
      queryInput.focus();
      queryInput.setAttribute('aria-invalid', 'true');
      queryHelp.firstElementChild.textContent = '请先输入一个问题。';
      window.setTimeout(() => {
        queryInput.removeAttribute('aria-invalid');
        queryHelp.firstElementChild.textContent = '审计阶段不调用模型、不访问数据库；结果查询以只读方式访问主库。';
      }, 2200);
      return;
    }

    state.queryBusy = true;
    queryInput.value = question;
    autoResize(queryInput);
    queryInput.removeAttribute('aria-invalid');
    querySubmit.disabled = true;
    querySubmitLabel.textContent = '正在审查';
    document.querySelectorAll('.preset-chip').forEach((button) => { button.disabled = true; });
    const outcome = buildOutcome(question);
    resultEmpty.hidden = true;
    resultPanel.hidden = false;
    resultPanel.setAttribute('aria-busy', 'true');
    resultPanel.innerHTML = processingHTML(outcome);

    const steps = Array.from(resultPanel.querySelectorAll('.trail-step'));
    const note = $('processNote');
    const notes = [
      '身份与数据范围已确认。',
      '正在生成可检查的查询计划……',
      outcome.status === 'denied' ? '一层准入发现越权风险。' : '正在判断请求是否在令牌范围内……',
      '正在进行二层安全审计……',
      '正在只读执行并整理结果……',
    ];

    if (outcome.status === 'denied') {
      window.setTimeout(() => { steps[0].classList.add('is-done'); }, 120);
      window.setTimeout(() => { steps[1].classList.add('is-current'); note.textContent = notes[1]; }, 320);
      window.setTimeout(() => {
        steps[1].classList.remove('is-current');
        steps[1].classList.add('is-done');
        steps[2].classList.add('is-denied');
        steps[3].classList.add('is-skipped');
        steps[4].classList.add('is-skipped');
        note.textContent = notes[2];
      }, 540);
    } else if (!outcome.executed) {
      window.setTimeout(() => { steps[0].classList.add('is-done'); }, 120);
      window.setTimeout(() => { steps[1].classList.add('is-current'); note.textContent = notes[1]; }, 300);
      window.setTimeout(() => {
        steps[1].classList.remove('is-current');
        steps[1].classList.add('is-warn');
        steps[2].classList.add('is-skipped');
        steps[3].classList.add('is-skipped');
        steps[4].classList.add('is-skipped');
        note.textContent = '没有找到范围明确的执行路径，查询停在主库之外。';
      }, 540);
    } else {
      steps.forEach((step, index) => {
        window.setTimeout(() => {
          steps.forEach((item) => item.classList.remove('is-current'));
          step.classList.add(index === steps.length - 1 ? 'is-done' : 'is-current');
          if (index > 0) steps[index - 1].classList.add('is-done');
          note.textContent = notes[Math.min(index + 1, notes.length - 1)];
        }, 120 + index * 170);
      });
      window.setTimeout(() => {
        steps.forEach((step) => { step.classList.remove('is-current'); step.classList.add('is-done'); });
      }, 1120);
    }

    const wait = outcome.status === 'denied' ? (reduceMotion ? 60 : 980) : (reduceMotion ? 60 : 1260);
    window.setTimeout(() => {
      state.queryBusy = false;
      querySubmit.disabled = false;
      querySubmitLabel.textContent = '开始查询';
      document.querySelectorAll('.preset-chip').forEach((button) => { button.disabled = false; });
      resultPanel.removeAttribute('aria-busy');
      outcome.id = nextReportId(new Date());
      outcome.time = new Date();
      reportStore.unshift(outcome);
      state.reportId = outcome.id;
      updateReportCounts();
      renderReportList();
      renderReportDetail();
      renderResult(outcome, false);
      if (state.view === 'ask') {
        const top = resultShell.getBoundingClientRect().top - appContent.getBoundingClientRect().top + appContent.scrollTop - 16;
        appContent.scrollTo({ top: Math.max(0, top), behavior: reduceMotion ? 'auto' : 'smooth' });
      }
    }, wait);
  }

  function nextReportId(date) {
    reportSequence += 1;
    return `AU-${dateStamp(date)}-${String(reportSequence).padStart(3, '0')}`;
  }

  /* ── 审计记录 ───────────────────────────────────── */
  function updateReportCounts() {
    reportCount.textContent = String(reportStore.length);
    reportListMeta.textContent = `${reportStore.length} 条 · 仅当前账号`;
  }

  function renderReportList() {
    if (!reportStore.length) {
      reportList.innerHTML = '<li class="report-empty">当前账号还没有查询审计记录。</li>';
      return;
    }
    reportList.innerHTML = reportStore.map((outcome) => `
      <li class="report-item">
        <button class="report-select" type="button" data-report="${outcome.id}" aria-pressed="${outcome.id === state.reportId}">
          <span class="report-item-top"><span class="report-id">${outcome.id}</span><span class="report-time">${relTime(outcome.time)}</span></span>
          <span class="report-q">${esc(outcome.question)}</span>
          <span class="report-item-bottom"><span class="mini-status ${outcome.status}">${outcome.statusText}</span><span class="report-ms">审计 ${outcome.auditMs} ms</span></span>
        </button>
      </li>`).join('');
  }

  function renderReportDetail() {
    const outcome = reportStore.find((item) => item.id === state.reportId) || reportStore[0];
    if (!outcome) {
      reportDetail.innerHTML = '<div class="report-empty">暂无审计记录。</div>';
      return;
    }
    state.reportId = outcome.id;
    reportDetail.innerHTML = `
      <div class="report-detail-head">
        <div class="report-detail-top">
          <div>
            <p class="eyebrow">AUDIT EVIDENCE / ${outcome.id}</p>
            <h2>${esc(outcome.question)}</h2>
            <div class="report-detail-meta">
              <span>${esc(session.displayName)}</span><span>${fullTime(outcome.time)}</span><span>范围 · ${meta.scopeName}</span>
            </div>
          </div>
          <button class="report-export" type="button" data-export="${outcome.id}">导出记录</button>
        </div>
        <div style="margin-top:12px"><span class="result-status ${outcome.status}">${outcome.statusText}</span></div>
      </div>
      <div class="report-detail-body">
        <div class="audit-grid">
          <div class="audit-cell"><span>审计 LLM</span><strong class="is-zero">0 次</strong></div>
          <div class="audit-cell"><span>审计数据库</span><strong class="is-zero">0 次</strong></div>
          <div class="audit-cell"><span>审计耗时</span><strong>${outcome.auditMs} ms</strong></div>
          <div class="audit-cell"><span>执行阶段</span><strong>${outcome.executed ? '主库只读 · 1 次' : '未启动'}</strong></div>
        </div>

        <section class="report-section">
          <h3>查询计划</h3>
          <ol class="plan-list">
            ${outcome.plan.map((step) => `<li><span><strong>${step.title}</strong><small>${step.detail}</small></span></li>`).join('')}
          </ol>
        </section>

        <section class="report-section">
          <h3>审计事件</h3>
          <ol class="timeline">
            ${outcome.events.map((event) => `<li class="${event.state === 'denied' ? 'is-denied' : ''}"><span>${event.text}</span><time>${event.time}</time></li>`).join('')}
          </ol>
        </section>

        <section class="report-section">
          <h3>改写与保护</h3>
          <div class="rewrite-list">
            ${outcome.rewrite.length ? outcome.rewrite.map((line) => `<div class="rewrite-line">${line}</div>`).join('') : '<div class="rewrite-line">请求在执行前终止，未发生字段改写。</div>'}
          </div>
        </section>

        <p class="report-footnote">审计层 LLM 0 / 数据库 0 仅统计审计阶段；执行成功时，查询本身会以只读方式访问医院主库。评估集上的越权拦截率（EX）为 62–77%，并非 100% 安全；小样本聚合推断不在当前防御范围内。本页数据均为虚构演示数据。</p>
      </div>`;
  }

  function exportReport(id) {
    const outcome = reportStore.find((item) => item.id === id);
    if (!outcome) return;
    const lines = [
      'MedGuard 安全审计记录（演示）',
      `记录编号：${outcome.id}`,
      `发起账号：${session.displayName}`,
      `数据范围：${meta.scopeName}`,
      `问题：${outcome.question}`,
      `状态：${outcome.statusText}`,
      `审计层：LLM 0 次 / 数据库 0 次 / 耗时 ${outcome.auditMs} ms`,
      `执行阶段：${outcome.executed ? `医院主库只读 ${outcome.execMs} ms` : '未启动'}`,
      '',
      '查询计划：',
      ...outcome.plan.map((step, index) => `${index + 1}. ${step.title} — ${step.detail}`),
      '',
      '审计事件：',
      ...outcome.events.map((event) => `- ${event.text}（${event.time}）`),
      '',
      '说明：审计层 LLM 0 / 数据库 0 仅统计审计阶段。评估集越权拦截率（EX）为 62–77%，并非 100% 安全；小样本聚合推断不在当前防御范围内。',
      '本记录来自演示环境，所有患者信息均为虚构测试数据。',
    ];
    const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${outcome.id}.txt`;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    showToast('审计记录已导出');
  }

  let toastTimer = 0;
  function showToast(message) {
    toast.textContent = message;
    toast.classList.add('is-on');
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove('is-on'), 2400);
  }

  /* ── 智慧医生 ───────────────────────────────────── */
  function chatTime() { return clock(new Date()); }

  function appendUser(text) {
    const message = document.createElement('div');
    message.className = 'msg is-user';
    message.innerHTML = `
      <span class="msg-avatar" aria-hidden="true">我</span>
      <div class="msg-main">
        <div class="msg-who"><span>${esc(session.displayName)}</span><time>${chatTime()}</time></div>
        <div class="msg-bubble"><p>${esc(text)}</p></div>
      </div>`;
    chatScroll.append(message);
    scrollChat();
  }

  function appendAssistant(answer) {
    const message = document.createElement('div');
    message.className = 'msg';
    message.innerHTML = `
      <span class="msg-avatar" aria-hidden="true">盾</span>
      <div class="msg-main">
        <div class="msg-who"><span>MedGuard 智慧医生</span><time>${chatTime()}</time></div>
        <div class="msg-bubble">
          <div class="answer-block">
            ${answer.sections.map((section) => `
              <section class="answer-section source-${section.kind}">
                <h5>${section.label}</h5><p>${section.text}</p>
              </section>`).join('')}
          </div>
          <div class="answer-foot"><span>来源已分离</span><span>${answer.foot || '不替代医生诊断'}</span></div>
        </div>
      </div>`;
    chatScroll.append(message);
    scrollChat();
  }

  function scrollChat() {
    chatScroll.scrollTop = chatScroll.scrollHeight;
  }

  function getChatAnswer(question) {
    const text = question.replace(/\s+/g, '');
    if (/血糖|6\.3|偏高/.test(text)) {
      return {
        sections: [
          { kind: 'hospital', label: '医院记录', text: '2025-04-05 心内科门诊：血糖 <strong>6.3 mmol/L</strong>。演示记录未注明是否空腹。' },
          { kind: 'plain', label: '通俗解释', text: '若为空腹静脉血糖，成人正常参考范围通常为 <strong>3.9–6.1 mmol/L</strong>。6.3 属于偏高（空腹血糖受损），但未达到 7.0 的糖尿病诊断参考线；若不是空腹，不能按这个标准判读。' },
          { kind: 'next', label: '下一步', text: '携带原报告咨询内分泌科医生，并在医生安排下复查空腹血糖与糖化血红蛋白。一次结果不足以自行诊断。' },
        ],
      };
    }
    if (/头孢|药怎么吃|用药|服药|剂量|每日三次/.test(text)) {
      return {
        sections: [
          { kind: 'hospital', label: '医院记录', text: '处方记录：头孢呋辛 <strong>1 片 / 每日三次 / 口服</strong>，记录日期 2025-04-05，开方科室为心内科门诊。' },
          { kind: 'plain', label: '通俗解释', text: '这是处方中写明的用法，不代表可以自行延长疗程或调整剂量。抗生素应按医生交代的疗程使用。' },
          { kind: 'next', label: '下一步', text: '具体疗程、过敏史和药物相互作用，请向开方医生或药师确认。出现皮疹、呼吸困难等过敏表现时立即停药并就医。' },
        ],
      };
    }
    if (/影像|MRI|核磁|腹部|报告怎么看/.test(text)) {
      return {
        sections: [
          { kind: 'hospital', label: '医院记录', text: '2025-04-05 腹部 MRI：结论为 <strong>未见异常（测试数据）</strong>。' },
          { kind: 'plain', label: '通俗解释', text: '“未见明显异常”表示本次影像中没有看到明确病变，但不等于所有症状都有了解释，也不能替代医生结合查体与化验判断。' },
          { kind: 'next', label: '下一步', text: '带原片和报告找开方医生复诊。如果症状持续或加重，不要只凭报告结论自行观察。' },
        ],
      };
    }
    if (/挂什么科|哪个科|分诊|不知道挂/.test(text)) {
      return {
        sections: [
          { kind: 'plain', label: '说明', text: '这套演示没有训练分诊模型，不能仅凭一句话可靠判断该挂哪个科。' },
          { kind: 'next', label: '下一步', text: '请补充症状部位、持续时间与伴随表现，或前往医院分诊台。胸痛、呼吸困难、意识改变等情况请立即前往急诊。' },
        ],
        foot: '不做自动分诊',
      };
    }
    if (/心内科.*医生|医生.*心内科|挂号|科室/.test(text)) {
      return {
        sections: [
          { kind: 'hospital', label: '医院审核知识库', text: '心内科在册医生：<strong>医生001</strong>（副主任医师，心血管疾病）、<strong>医生002</strong>（主治医师，冠心病介入）。' },
          { kind: 'plain', label: '通俗解释', text: '两位医生的专长方向不同，具体选择应结合你的症状与既往检查。这里不根据一句话做分诊判断。' },
          { kind: 'next', label: '下一步', text: '出诊时间与号源以医院当日公示为准。胸痛、呼吸困难、意识改变等情况请立即前往急诊。' },
        ],
      };
    }
    if (/糖尿病|疾病|了解/.test(text)) {
      return {
        sections: [
          { kind: 'plain', label: '说明', text: '请告诉我想了解的具体疾病名称，例如“2 型糖尿病”。知识库会说明常见定义、检查与生活管理，但不会给出个体化处方。' },
          { kind: 'next', label: '下一步', text: '你可以继续问“我想了解糖尿病”，或把具体检查指标发给我。' },
        ],
        foot: '需要更具体的疾病名称',
      };
    }
    return {
      sections: [
        { kind: 'plain', label: '说明', text: '这个问题还没有命中医院审核知识库。为避免把相似内容拼成确定答案，我不会猜测。' },
        { kind: 'next', label: '下一步', text: '换用更具体的指标、药品或报告名称再问一次，例如“我的血糖 6.3 偏高吗”。' },
      ],
      foot: '未命中审核知识库',
    };
  }

  function sendChat(raw) {
    if (state.chatBusy) return;
    const question = String(raw || '').trim();
    if (!question) { chatInput.focus(); return; }
    state.chatBusy = true;
    chatInput.value = '';
    autoResize(chatInput);
    chatSend.disabled = true;
    chatChips.querySelectorAll('button').forEach((button) => { button.disabled = true; });
    appendUser(question);

    const typing = document.createElement('div');
    typing.className = 'msg';
    typing.id = 'chatTyping';
    typing.innerHTML = `
      <span class="msg-avatar" aria-hidden="true">盾</span>
      <div class="msg-main">
        <div class="msg-who"><span>MedGuard 智慧医生</span><time>${chatTime()}</time></div>
        <div class="msg-bubble"><span class="typing" aria-label="正在核对医院记录与审核知识库"><i></i><i></i><i></i></span></div>
      </div>`;
    chatScroll.append(typing);
    scrollChat();

    window.setTimeout(() => {
      typing.remove();
      appendAssistant(getChatAnswer(question));
      state.chatBusy = false;
      chatSend.disabled = false;
      chatChips.querySelectorAll('button').forEach((button) => { button.disabled = false; });
    }, reduceMotion ? 80 : 820);
  }

  function paintChatIntro() {
    const intro = document.createElement('div');
    intro.className = 'msg';
    intro.innerHTML = `
      <span class="msg-avatar" aria-hidden="true">盾</span>
      <div class="msg-main">
        <div class="msg-who"><span>MedGuard 智慧医生</span><time>${chatTime()}</time></div>
        <div class="msg-bubble">
          <p>你好，${esc(session.displayName)}。我会把<strong>医院记录</strong>、<strong>通俗解释</strong>和<strong>下一步建议</strong>分开说明。你可以先问检验、用药或影像报告。</p>
          <div class="answer-foot"><span>来源分离</span><span>演示患者 · 患者001</span></div>
        </div>
      </div>`;
    chatScroll.append(intro);
  }

  /* ── 视图切换 ───────────────────────────────────── */
  function applyView(name) {
    state.view = name;
    document.querySelectorAll('.view').forEach((panel) => {
      const active = panel.id === `view-${name}`;
      panel.hidden = !active;
      panel.classList.toggle('is-active', active);
    });
    document.querySelectorAll('.work-link').forEach((button) => {
      button.setAttribute('aria-selected', String(button.dataset.view === name));
    });
    $('topTitle').textContent = viewMeta[name].title;
    document.title = viewMeta[name].docTitle;
    appContent.scrollTo({ top: 0, behavior: 'auto' });
    if (name === 'reports') {
      renderReportList();
      renderReportDetail();
    }
    history.replaceState(null, '', `#${name}`);
    closeSidebar();
  }

  function switchView(name, event) {
    if (!viewMeta[name] || name === state.view) {
      closeSidebar();
      return;
    }
    if (reduceMotion || !event) {
      applyView(name);
      return;
    }
    const x = event.clientX || window.innerWidth / 2;
    const y = event.clientY || window.innerHeight / 3;
    viewWash.style.setProperty('--wash-x', `${x}px`);
    viewWash.style.setProperty('--wash-y', `${y}px`);
    viewWash.classList.remove('is-leaving');
    viewWash.classList.add('is-active');
    window.setTimeout(() => applyView(name), 190);
    window.setTimeout(() => viewWash.classList.add('is-leaving'), 340);
    window.setTimeout(() => viewWash.classList.remove('is-active'), 690);
    window.setTimeout(() => viewWash.classList.remove('is-leaving'), 760);
  }

  /* ── 移动端侧栏 ─────────────────────────────────── */
  const menuToggle = $('menuToggle');
  const appSide = $('appSide');
  const sideScrim = $('sideScrim');

  function openSidebar() {
    appSide.classList.add('is-open');
    sideScrim.classList.add('is-on');
    menuToggle.setAttribute('aria-expanded', 'true');
    document.body.classList.add('nav-open');
  }
  function closeSidebar() {
    appSide.classList.remove('is-open');
    sideScrim.classList.remove('is-on');
    menuToggle.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('nav-open');
  }

  /* ── 工具 ───────────────────────────────────────── */
  function autoResize(field) {
    field.style.height = 'auto';
    field.style.height = `${Math.min(field.scrollHeight, field === queryInput ? 138 : 100)}px`;
  }

  /* ── 初始化 ─────────────────────────────────────── */
  paintIdentity();
  paintPresets();
  paintChatIntro();

  const seedQuestions = {
    staff: ['统计各科室接诊量', '糖尿病患者产生了多少费用', '导出患者基本信息核对表'],
    patient: ['我上次的血糖是多少', '医生给我开的药怎么吃', '心内科有哪些医生'],
    admin: ['统计各科室接诊量', '按诊断结果分类统计患者数', '导出患者基本信息核对表'],
  };
  const seedOffsets = [32 * 60 * 1000, 3 * 60 * 60 * 1000, 26 * 60 * 60 * 1000];

  seedQuestions[role].forEach((question, index) => {
    const outcome = buildOutcome(question);
    outcome.time = new Date(Date.now() - seedOffsets[index]);
    outcome.id = nextReportId(outcome.time);
    reportStore.push(outcome);
  });
  state.reportId = reportStore[0].id;
  updateReportCounts();
  renderReportList();
  renderReportDetail();
  if (reportStore[0]) renderResult(reportStore[0], true);

  const validStart = ['ask', 'doctor', 'reports'].includes(location.hash.replace('#', ''))
    ? location.hash.replace('#', '')
    : (isPatient ? 'doctor' : 'ask');
  applyView(validStart);
  if (validStart === 'ask' && reportStore[0]) renderResult(reportStore[0], true);

  /* 事件绑定 */
  document.querySelectorAll('.work-link').forEach((button) => {
    button.addEventListener('click', (event) => switchView(button.dataset.view, event));
  });

  queryForm.addEventListener('submit', (event) => {
    event.preventDefault();
    runQuery(queryInput.value);
  });
  queryInput.addEventListener('input', () => {
    autoResize(queryInput);
    queryInput.removeAttribute('aria-invalid');
  });
  queryInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      queryForm.requestSubmit();
    }
  });
  resultPanel.addEventListener('click', (event) => {
    const fill = event.target.closest('[data-fill]');
    if (!fill) return;
    queryInput.value = fill.dataset.fill;
    autoResize(queryInput);
    queryForm.requestSubmit();
  });

  reportList.addEventListener('click', (event) => {
    const button = event.target.closest('[data-report]');
    if (!button) return;
    state.reportId = button.dataset.report;
    renderReportList();
    renderReportDetail();
  });
  reportDetail.addEventListener('click', (event) => {
    const button = event.target.closest('[data-export]');
    if (button) exportReport(button.dataset.export);
  });

  chatForm.addEventListener('submit', (event) => {
    event.preventDefault();
    sendChat(chatInput.value);
  });
  chatInput.addEventListener('input', () => autoResize(chatInput));
  chatInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      chatForm.requestSubmit();
    }
  });
  chatChips.addEventListener('click', (event) => {
    const button = event.target.closest('[data-chat]');
    if (button) sendChat(button.dataset.chat);
  });

  menuToggle.addEventListener('click', openSidebar);
  $('sideClose').addEventListener('click', () => {
    closeSidebar();
    menuToggle.focus();
  });
  sideScrim.addEventListener('click', closeSidebar);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && appSide.classList.contains('is-open')) {
      closeSidebar();
      menuToggle.focus();
    }
    if (event.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && state.view === 'ask') {
      event.preventDefault();
      queryInput.focus();
    }
  });
  window.addEventListener('resize', () => {
    if (window.innerWidth > 980 && appSide.classList.contains('is-open')) closeSidebar();
  });

  if (!reduceMotion) {
    let pointerFrame = null;
    appMain.addEventListener('pointermove', (event) => {
      if (pointerFrame) return;
      pointerFrame = requestAnimationFrame(() => {
        const rect = appMain.getBoundingClientRect();
        const x = Math.max(0, Math.min(100, (event.clientX - rect.left) / rect.width * 100));
        const y = Math.max(0, Math.min(100, (event.clientY - rect.top) / rect.height * 100));
        appMain.style.setProperty('--px', `${x.toFixed(1)}%`);
        appMain.style.setProperty('--py', `${y.toFixed(1)}%`);
        pointerFrame = null;
      });
    }, { passive: true });
  }
})();
