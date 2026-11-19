/* JobAgent 前端：Vue 3（本地 vendor，无构建步骤）
 * 契约与后端严格对齐：POST /api/chat、POST /api/upload、GET /api/login_status、GET /api/tasks/{id}
 * 所有文案插值一律走 Vue 文本绑定，不用 v-html——岗位标题、文件名都是外部数据。
 */
const SENDER_KEY = 'sender_id';

// 与后端话术的字符串耦合：这句改了，左脊的意向进度判定就会失真
const COPY = {
  intentDone: '已记录你的求职意向',      // action_save_intent
};

// 登录窗口体检轮询间隔：本地接口，2 秒足够跟上手速又不在后台空转
const LOGIN_POLL_MS = 2000;
// 与后端 LOGIN_CONFIRMED 对齐：collect 槽按这个值放行，改了后端要一起改
const LOGIN_CONFIRMED = '已登录';

let seq = 0;
const nextId = () => `m${++seq}`;

const api = {
  async chat(body) {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return resp.json();
  },
  async upload(file) {
    const form = new FormData();
    form.append('file', file);
    return (await fetch('/api/upload', { method: 'POST', body: form })).json();
  },
  async task(id) {
    const resp = await fetch(`/api/tasks/${id}`);
    return resp.json();
  },
  async loginStatus(senderId) {
    const resp = await fetch(`/api/login_status?sender_id=${encodeURIComponent(senderId)}`);
    return resp.json();
  },
  async panel() {
    const resp = await fetch('/api/panel');
    return resp.json();
  },
  async setPanel(action) {
    const resp = await fetch('/api/panel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    return resp.json();
  },
  async settings() {
    const resp = await fetch('/api/settings');
    return resp.json();
  },
  async saveSettings(body) {
    const resp = await fetch('/api/settings/llm', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return resp.json();
  },
  async clearHistory(senderId) {
    const resp = await fetch(`/api/history/${encodeURIComponent(senderId)}`, { method: 'DELETE' });
    return resp.json();
  },
};

/* ── 卡片组件 ─────────────────────────────────────────────── */

const SetupCard = {
  name: 'SetupCard',
  props: ['settings'],
  emits: ['saved', 'close'],
  template: '#tpl-setup-card',
  data() {
    // base_url / model 预填当前值：表单里"清空"和"没动过"才分得开
    return {
      api_key: '', reveal: false, busy: false, error: '', saved: '',
      search_key: '', revealSearch: false,
      base_url: this.settings.llm_base_url || '',
      model: this.settings.llm_model || '',
    };
  },
  computed: {
    locked() { return !this.settings.llm_key_set; },
    hintKey() {
      return this.locked ? 'sk-…（粘贴你的 Key）'
        : `已存 ${this.settings.llm_key_masked}（${this.settings.llm_key_store}），留空则不改`;
    },
    hintSearchKey() {
      return this.settings.search_key_set
        ? `已存 ${this.settings.search_key_masked}（${this.settings.search_key_store}），留空则不改`
        : 'bce-v3/…（百度千帆的 Key，不填就不联网搜索）';
    },
    dirty() {
      return !!this.api_key || !!this.search_key
        || this.base_url !== (this.settings.llm_base_url || '')
        || this.model !== (this.settings.llm_model || '');
    },
  },
  methods: {
    async save() {
      if (!this.dirty || this.busy) return;
      this.busy = true;
      this.error = '';
      this.saved = '';
      let data;
      try {
        data = await api.saveSettings({
          api_key: this.api_key || null, base_url: this.base_url, model: this.model,
          search_api_key: this.search_key || null,
        });
      } catch (e) {
        data = null;
      }
      this.busy = false;
      if (!data) { this.error = '没存进去：后端没在跑，或网络断了。'; return; }
      if (data.code) { this.error = `错误 ${data.code}：${data.message}`; return; }
      this.api_key = '';
      this.reveal = false;
      this.search_key = '';
      this.revealSearch = false;
      this.saved = `已保存 · 现在用 ${data.llm_model}${data.llm_base_url ? '' : '（官方地址）'}`;
      this.$emit('saved', data);
    },
  },
};

const LoginCard = {
  name: 'LoginCard',
  props: ['msg', 'live'],   // live：null=还没体检过，false=这张卡片已不属于当前轮，对象=最近一次 /api/login_status
  emits: ['confirm', 'reopen'],
  template: '#tpl-login-card',
  data() { return { busy: false }; },
  computed: {
    payload() { return this.msg.card.payload || {}; },
    // 登录与否由后端 cookie 判定说了算，payload.status 只是后端当时怎么说
    phase() {
      if (this.payload.status === 'logged_in') return 'done';
      if (this.payload.status === 'canceled') return 'canceled';
      if (this.live === false) return 'stale';
      if (!this.live) return 'check';
      if (this.live.logged_in) return 'found';
      if (!this.live.window_open) return 'closed';
      return 'waiting';
    },
    phaseLabel() {
      return {
        done: '已登录', found: '检测到登录', waiting: '等你登录',
        closed: '登录页没显示', stale: '这一轮已结束', check: '确认中',
        canceled: '已取消',
      }[this.phase];
    },
    line() {
      return {
        waiting: 'BOSS直聘 登录页已经打开了：在那个页面里登录，App 扫码、手机号、密码都行；'
          + '出现安全验证就顺手完成它。登录成功后我接着给你筛岗位，这块页面不用你关。',
        check: '正在确认登录状态…',
        found: '读到登录态了，正在收尾，稍等一下。',
        closed: '登录页现在没显示出来。点下面的按钮把它打开，登录完我自动继续。',
        stale: '这张卡片已经不是当前这一轮登录了。要再登录一次，点下面的按钮。',
        canceled: '登录已取消。想继续时说「登录」或点下面的按钮，我再开一次。',
        done: '已登录 BOSS直聘：登录态存在本机浏览器目录里，下次不用再登。',
      }[this.phase];
    },
  },
  watch: {
    // 轮询是唯一的触发点：读到登录态就替用户提交一次确认（busy 防同一轮里翻来覆去地提交）
    phase(to) {
      if (to === 'found' && !this.busy) {
        this.busy = true;
        this.$emit('confirm', 'auto');
      }
    },
  },
};

const JobCard = {
  name: 'JobCard',
  props: ['msg'],
  emits: ['greet'],
  template: '#tpl-job-card',
  data() { return { picked: [] }; },
  computed: {
    jobs() {
      const raw = this.msg.card.payload.jobs || [];
      return raw.map((j, i) => ({
        id: j.id ?? String(i),
        title: j.title || j.name || '未命名岗位',
        company: j.company || '公司未提供',
        city: j.city || '',
        salary: j.salary || '薪资未公开',
        score: Number(j.score ?? j.match_score ?? 0),
      }));
    },
  },
  methods: {
    on(id) { return this.picked.includes(id); },
    toggle(id) {
      const i = this.picked.indexOf(id);
      if (i < 0) this.picked.push(id); else this.picked.splice(i, 1);
    },
  },
};

const ResumeCard = {
  name: 'ResumeCard',
  props: ['msg'],
  template: '#tpl-resume-card',
  computed: {
    s() { return this.msg.card.payload || {}; },
    facts() {
      const s = this.s;
      const skills = (s.skills || []).join('、');
      const exps = (s.experiences || [])
        .map(e => [e.company, e.title || e.position].filter(Boolean).join(' · ')).join('；');
      const projs = (s.projects || []).map(p => p.name).filter(Boolean).join('；');
      return [
        { k: '期望岗位', v: s.expected_position || '未检出' },
        { k: '城市', v: s.city || s.expected_city || '未检出' },
        { k: '技能', v: skills || '未检出' },
        { k: '经历', v: exps || '未检出' },
        { k: '项目', v: projs || '未检出' },
      ];
    },
  },
};

const TaskCard = {
  name: 'TaskCard',
  props: ['msg', 'live'],
  template: '#tpl-task-card',
  computed: {
    payload() { return this.msg.card.payload || {}; },
    dryRun() { return this.live ? !!this.live.dry_run : !!this.payload.dry_run; },
    total() { return this.live?.total ?? this.payload.total ?? 0; },
    items() {
      return (this.live?.items || []).map(i => ({
        job_id: i.job_id,
        title: i.title,
        failure_reason: i.failure_reason,
        kind: { PENDING: 'wait', SUCCEEDED: 'ok', DRY_RUN: 'dry', FAILED: 'bad' }[i.item_status] || 'wait',
      }));
    },
    finished() { return !!this.live?.finished; },
    head() {
      if (!this.live) return '沟通任务已排队';
      if (!this.finished) return '正在逐岗位沟通';
      return '沟通结果';
    },
    summary() {
      const done = this.items.filter(i => i.kind !== 'wait').length;
      if (!this.live) return `共 ${this.total} 个岗位，等待后端开工。`;
      if (!this.finished) return `共 ${this.total} 个 · 已完成 ${done}，跑完自动更新。`;
      return `共 ${this.live.total} 个 · 已沟通 ${this.live.succeeded} · 未成功 ${this.live.failed}`
        + (this.dryRun ? ' · 演练模式，没有真的发消息' : '');
    },
    failures() { return this.items.filter(i => i.kind === 'bad'); },
  },
};

const GenericCard = {
  name: 'GenericCard',
  props: ['msg'],
  template: '#tpl-generic-card',
  computed: {
    type() { return this.msg.card.type; },
    raw() { return JSON.stringify(this.msg.card.payload, null, 2); },
  },
};

/* ── 根组件 ───────────────────────────────────────────────── */

const app = Vue.createApp({
  data() {
    let senderId = localStorage.getItem(SENDER_KEY);
    if (!senderId) {
      senderId = (crypto.randomUUID?.() || `u${Date.now()}`);
      localStorage.setItem(SENDER_KEY, senderId);
    }
    return {
      senderId,
      settings: null,         // /api/settings；null = 还没拿到或不支持
      settingsOpen: false,
      messages: [],
      draft: '',
      pending: null,        // { label, startedAt }
      pendingSeconds: 0,
      tasks: {},            // task_id → /api/tasks 最近一次结果
      loginLive: null,      // 最近一次 /api/login_status；null = 还没体检
      panel: null,          // /api/panel：BOSS 左栏状态；null = 还没拿到，supported=false 不显示按钮
      openers: ['我想找工作', '上传简历', '登录', '查询岗位'],
      // 左脊只写状态，怎么操作由「下一步」条说，两处不重复同一句话
      stages: [
        { key: 'resume', name: '简历', wait: '待上传', hint: '点左下「上传简历」，PDF 或 Word 都行' },
        { key: 'intent', name: '意向', wait: '待填写', hint: '告诉我岗位方向，比如「Python 后端」' },
        { key: 'login', name: '登录', wait: '待登录', hint: '说「登录」，我把 BOSS直聘 登录页弹到你桌面上' },
        { key: 'jobs', name: '岗位', wait: '待筛选', hint: '说「查询岗位」，我会按简历匹配候选' },
        { key: 'greet', name: '沟通', wait: '待投递', hint: '勾好岗位后点「打招呼投递」' },
      ],
      selectionSent: false,
    };
  },

  computed: {
    shortId() { return this.senderId.slice(0, 8); },

    needKey() { return !!this.settings && !this.settings.llm_key_set; },

    // 没 Key 就挡在设置页；有 Key 时只有点了「模型设置」才进。settings 没拿到前不进（组件按它渲染）
    showSettings() { return !!this.settings && (this.needKey || this.settingsOpen); },

    doneMap() {
      const joined = this.messages.filter(m => m.kind === 'text').map(m => m.text || '').join('\n');
      const cards = this.messages.filter(m => m.kind === 'card').map(m => m.card);
      const types = cards.map(c => c.type);
      const anyTask = Object.values(this.tasks);
      // 门禁反推：后端只会在意向确认后开登录窗、登录成功后出岗位卡片，
      // 所以卡片本身就是进度证据；登录更直接——卡片自带后端判定过的 status
      const intentDone = joined.includes(COPY.intentDone)
        || types.includes('login_window') || types.includes('job_list');
      const loginDone = cards.some(c => c.type === 'login_window'
        && (c.payload || {}).status === 'logged_in') || types.includes('job_list');
      return {
        resume: types.includes('resume'),
        intent: intentDone,
        login: loginDone,
        jobs: this.selectionSent || anyTask.length > 0,
        greet: anyTask.some(t => t && t.finished),
      };
    },

    // 当前该轮询的登录卡片：最后一张且后端说还在等；历史卡片一律不碰
    loginCardId() {
      const last = [...this.messages].reverse().find(m => m.kind === 'card');
      if (!last || last.card.type !== 'login_window') return '';
      const status = (last.card.payload || {}).status;
      return (status === 'logged_in' || status === 'canceled') ? '' : last.id;
    },

    rail() {
      const done = this.doneMap;
      let activeTaken = false;
      return this.stages.map(s => {
        if (done[s.key]) return { ...s, status: 'done' };
        if (activeTaken) return { ...s, status: 'todo' };
        activeTaken = true;
        return { ...s, status: 'active' };
      });
    },

    activeKey() {
      const done = this.doneMap;
      return this.stages.find(s => !done[s.key])?.key || 'greet';
    },

    nextBar() {
      const stage = this.stages.find(s => s.key === this.activeKey);
      return { text: this.activeHint(stage.key), waiting: this.awaitingCard };
    },

    // 屏幕上最后一张待操作卡片：这时"下一步"就在眼前，不用再复述流程
    awaitingCard() {
      const last = [...this.messages].reverse().find(m => m.kind === 'card');
      if (!last) return false;
      return (this.activeKey === 'login' && last.card.type === 'login_window')
        || (this.activeKey === 'jobs' && last.card.type === 'job_list');
    },
  },

  watch: {
    // 有待确认的登录卡片才轮询；卡片翻篇（登录成功/换成别的卡）即停
    loginCardId(to) {
      clearInterval(this.loginTimer);
      this.loginLive = null;
      if (!to) return;
      const tick = async () => {
        try {
          const data = await api.loginStatus(this.senderId);
          if (data && !data.code) this.loginLive = data;
        } catch (e) {
          clearInterval(this.loginTimer);   // 后端断了别对着空气重试
        }
      };
      this.loginTimer = setInterval(tick, LOGIN_POLL_MS);
      tick();
    },
  },

  mounted() {
    this.$refs.thread && this.scrollToEnd();
    this.loadSettings();
    this.loadPanel();
  },

  methods: {
    /* ---- 模型设置 ---- */
    async loadSettings() {
      try {
        const data = await api.settings();
        if (data && !data.code) this.settings = data;
      } catch (e) {
        // 老后端没这个接口：照常跑，缺 Key 时由后端的 7001 兜住
      }
    },
    async loadPanel() {
      try {
        const data = await api.panel();
        if (data && !data.code) this.panel = data;
      } catch (e) {
        // 老后端没这个接口：不显示折叠按钮就是了
      }
    },
    async togglePanel() {
      if (!this.panel || !this.panel.supported) return;
      try {
        const data = await api.setPanel(this.panel.presented ? 'collapse' : 'reveal');
        if (data && data.code) { this.pushError(data.code, data.message); return; }
        this.panel = data;
      } catch (e) {
        this.pushError(0, '切换 BOSS 页面失败：后端没在跑，或网络断了。');
      }
    },
    openSettings() { this.settingsOpen = true; },
    settingsSaved(data) {
      this.settings = data;
      this.settingsOpen = !data.llm_key_set;   // 存完还有 Key 缺失就继续挡着
      this.scrollToEnd();
    },

    activeHint(key) {
      const last = [...this.messages].reverse().find(m => m.kind === 'card');
      if (key === 'login' && last?.card.type === 'login_window') {
        return '在打开的登录页里登录，好了我自动继续。';
      }
      if (key === 'jobs' && last?.card.type === 'job_list') {
        return '在卡片里勾出要投的岗位，然后点「打招呼投递」。';
      }
      if (key === 'greet' && this.doneMap.greet) {
        return '本轮跑完了。可以说「查询岗位」换一批，或「退出登录」收工。';
      }
      return this.stages.find(s => s.key === key).hint + '。';
    },

    /* ---- 消息流 ---- */
    pushUser(text) {
      this.messages.push({ id: nextId(), role: 'user', kind: 'text', text });
      this.scrollToEnd();
      return this.messages[this.messages.length - 1];
    },
    pushBot(msg) {
      // 后端一条回复可能是多条：文本 / 卡片分别落进流里
      if (msg.type === 'OBJECT' && msg.object) {
        this.messages.push({ id: nextId(), role: 'bot', kind: 'card', card: msg.object });
        if (msg.object.type === 'task_report') this.trackTask(msg.object.id);
      } else if (msg.text) {
        this.messages.push({ id: nextId(), role: 'bot', kind: 'text', text: msg.text,
                             choices: msg.choices || [] });
      }
      this.scrollToEnd();
    },
    pushError(code, message) {
      this.messages.push({
        id: nextId(), role: 'bot', kind: 'text', level: 'error',
        text: code ? `错误 ${code}：${message}` : message,
      });
      this.scrollToEnd();
    },

    async post(body, label) {
      this.startPending(label);
      try {
        const data = await api.chat(body);
        this.stopPending();
        if (data.code) {
          // 7001 = 后端发现没 Key：把设置页拉起来，别让用户对着错误气泡猜
          if (data.code === 7001) this.loadSettings();
          this.pushError(data.code, data.message);
          return data;
        }
        (data.messages || []).forEach(this.pushBot);
        // 后端可能在这轮里自动展开了左栏（登录/风控），按钮状态跟着刷新
        if (this.panel && this.panel.supported) this.loadPanel();
        return data;
      } catch (e) {
        this.stopPending();
        this.messages.push({
          id: nextId(), role: 'bot', kind: 'text', level: 'error', failed: true, failedBody: body,
          text: '这条没送出去：后端没在跑，或网络断了。启动服务后点重发。',
        });
        this.scrollToEnd();
        return null;
      }
    },

    startPending(label) {
      this.pending = { label, startedAt: Date.now() };
      this.pendingSeconds = 0;
      this.pendingTimer = setInterval(() => {
        this.pendingSeconds = Math.floor((Date.now() - this.pending.startedAt) / 1000);
      }, 1000);
    },
    stopPending() {
      clearInterval(this.pendingTimer);
      this.pending = null;
    },

    /* ---- 动作 ---- */
    pickChoice(m, choice) {
      // 快捷回复 = 替用户打出那句话：走与手打完全相同的规划链路；点过即收起按钮
      if (this.pending) return;
      m.choices = [];
      this.say(choice);
    },
    say(text) {
      // 「上传简历」不是要发给后端的话，是打开文件选择器
      if (text === '上传简历') { this.$refs.file.click(); return; }
      this.draft = text;
      this.send();
    },
    async send() {
      const text = this.draft;
      if (!text || this.pending) return;
      this.pushUser(text);
      this.draft = '';
      await this.post({ sender_id: this.senderId, type: 'TEXT', text }, '正在处理');
    },
    async resend(m) {
      if (!m.failedBody) return;
      this.messages.splice(this.messages.indexOf(m), 1);
      await this.post(m.failedBody, '正在重发');
    },

    async uploadResume(ev) {
      const file = ev.target.files[0];
      if (!file) return;
      this.pushUser(`上传简历：${file.name}`);
      this.startPending('正在上传归档');
      let data;
      try {
        data = await api.upload(file);
      } catch (e) {
        this.stopPending();
        this.pushError(0, '上传失败：后端没在跑，或网络断了。');
        return;
      }
      this.stopPending();
      ev.target.value = '';
      if (data.code) { this.pushError(data.code, data.message); return; }
      // 上传成功只是拿到 file_ref；真正驱动流程的是这条 OBJECT 消息
      await this.post({
        sender_id: this.senderId, type: 'OBJECT',
        object: { type: 'resume', id: data.file_ref, payload: { filename: data.filename } },
      }, '正在解析简历');
    },

    confirmLogin() {
      // 只带 login_confirmed 这一个值：login_status 由后端读 cookie 判定，前端不许写
      this.pushUser('检测到已登录，继续');
      this.post({
        sender_id: this.senderId, type: 'OBJECT',
        object: { type: 'login_window', id: 'login_window', payload: { login_confirmed: LOGIN_CONFIRMED } },
      }, '正在确认登录态');
    },

    reopenLogin() {
      // 走文本让规划器重开流程：boss_login 标了重进即重新开窗
      this.say('重新登录');
    },

    async clearHistory() {
      if (!window.confirm('清空后对话和流程进度都会丢（意向、简历摘要要重新来），但不会退出登录。确定吗？')) return;
      try {
        const data = await api.clearHistory(this.senderId);
        if (data && data.code) { this.pushError(data.code, data.message); return; }
        Object.values(this.taskTimers).forEach(clearInterval);
        this.taskTimers = {};
        this.stopPending();
        this.messages = [];
        this.tasks = {};
        this.loginLive = null;
        this.selectionSent = false;
        this.messages.push({
          id: nextId(), role: 'bot', kind: 'text',
          text: '会话已清空。我们从第一步重新开始：先上传简历，或者直接告诉我你的求职方向。',
        });
        this.scrollToEnd();
      } catch (e) {
        this.pushError(0, '清空失败：后端没在跑，或网络断了。');
      }
    },

    async greetJobs(ids) {
      this.selectionSent = true;
      this.pushUser(`给 ${ids.length} 个岗位打招呼`);
      // 顺序不能反：先把勾选写进 job_selection 槽，再让规划器起打招呼流程
      await this.post({
        sender_id: this.senderId, type: 'OBJECT',
        object: { type: 'job_list', id: 'selected', payload: { job_selection: ids } },
      }, '正在记录勾选');
      await this.post({ sender_id: this.senderId, type: 'TEXT', text: '给勾选的岗位打招呼' }, '正在开工');
    },

    /* ---- 打招呼任务轮询：卡片在流里，进度靠 /api/tasks ---- */
    trackTask(taskId) {
      if (this.tasks[taskId]) return;
      this.tasks[taskId] = null;            // 先占位，卡片才知道要显示"已排队"
      const tick = async () => {
        try {
          const data = await api.task(taskId);
          if (data.code) { this.pushError(data.code, data.message); clearInterval(this.taskTimers[taskId]); return; }
          this.tasks[taskId] = data;
          if (data.finished) clearInterval(this.taskTimers[taskId]);
        } catch (e) {
          clearInterval(this.taskTimers[taskId]);
        }
      };
      this.taskTimers[taskId] = setInterval(tick, 5000);
      tick();
    },

    scrollToEnd() {
      this.$nextTick(() => {
        const el = this.$refs.thread;
        if (el) el.scrollTop = el.scrollHeight;
      });
    },
  },

  created() { this.taskTimers = {}; this.loginTimer = null; },
  unmounted() {
    Object.values(this.taskTimers).forEach(clearInterval);
    clearInterval(this.loginTimer);
  },
});

Object.entries({ SetupCard, LoginCard, JobCard, ResumeCard, TaskCard, GenericCard })
  .forEach(([name, comp]) => app.component(name.replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase(), comp));

app.mount('#app');
