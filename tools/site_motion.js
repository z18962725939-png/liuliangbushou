/* ═══════════════════════════════════════════════════════════════
   流量捕手官网 · 互动层 v3
   ─────────────────────────────────────────────────────────────
   零依赖、原生实现。由 tools/site_seo.py 内联进每个页面。

   ★ 两条铁律，改的时候别破：

   1. 渐进增强。所有内容在 HTML 里都是静态可见的，脚本只负责「播放」。
      爬虫、审核机器人、关了 JS 的浏览器看到的必须是完整内容，
      不是一片空白 —— 这一层的失败会直接让 SEO 掉光。

   2. 只动 transform 和 opacity。动 width/height/top/left 会让浏览器
      每帧重排重绘，肉眼就该觉得卡。
      时值在 CSS 令牌里（--t-*），这边复用的都是同一套，不要裸写数字。

   另外：整段包在 IIFE 里，不往 window 上挂东西 —— build_site.py 那段
   页面脚本已经占了 openPay / toggleMenu 这些名字，别跟它打架。
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // 尊重系统的「减少动态效果」偏好：这里只关掉位移类的观赏动画，
  // 保留展开、选中态这些能帮人理解界面的反馈。
  var REDUCE = window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;

  var doc = document;
  var $ = function (s, r) { return (r || doc).querySelector(s); };
  var $$ = function (s, r) {
    return Array.prototype.slice.call((r || doc).querySelectorAll(s));
  };

  // 加 .js 而不是去掉 .no-js：只有这一行跑到了，CSS 才允许把
  // .rv 元素先藏起来准备演出。脚本没跑 = 不加这个类 = 内容照常显示。
  doc.documentElement.classList.add('js');

  /* ───────────────────────────────────────────────────────────
     1. 滚动：导航收起 + 2px 进度条
        这两个动作每天触发几百次，必须最轻 —— 只读一次 scrollY，
        所有写入批在一次 rAF 里做，绝不每帧都碰 DOM。
     ─────────────────────────────────────────────────────────── */
  (function scrollFx() {
    var nav = $('.nav');
    if (!nav) return;

    var prog = doc.createElement('div');
    prog.className = 'prog';
    nav.appendChild(prog);

    var ticking = false;
    function paint() {
      var y = window.pageYOffset || doc.documentElement.scrollTop;
      var h = doc.documentElement.scrollHeight - window.innerHeight;
      nav.classList.toggle('scrolled', y > 24);
      prog.style.width = (h > 0 ? Math.min(1, y / h) * 100 : 0) + '%';
      ticking = false;
    }
    function onScroll() {
      if (!ticking) { ticking = true; window.requestAnimationFrame(paint); }
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    paint();
  })();

  /* ───────────────────────────────────────────────────────────
     2. 进视口再演出
        threshold 0.12 + 负 margin：元素露头一小截才起动，
        既不会太早演完，也不会晚到用户已经读过才发现它在淡入。
     ─────────────────────────────────────────────────────────── */
  (function reveal() {
    var items = $$('.rv');
    if (!items.length) return;

    if (REDUCE || !('IntersectionObserver' in window)) {
      items.forEach(function (el) { el.classList.add('in'); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add('in');
        io.unobserve(e.target);      // 只演一次，别搞来回味
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });

    items.forEach(function (el) { io.observe(el); });

    // 兜底：3 秒后把还没演的全部放开。
    // 一旦前面某个环节出错，最坏的结果是「没有入场动画」，
    // 而不是「页面上有一整块永远空白」——后一种是事故，前一种只是可惜。
    window.setTimeout(function () {
      items.forEach(function (el) { el.classList.add('in'); });
    }, 3000);
  })();

  /* ───────────────────────────────────────────────────────────
     3. 数字滚动：一个数只演一次，滚回去不重播
     ─────────────────────────────────────────────────────────── */
  (function counters() {
    var els = $$('[data-count]');
    if (!els.length) return;

    function run(el) {
      var target = parseFloat(el.getAttribute('data-count')) || 0;
      var suffix = el.getAttribute('data-suffix') || '';
      if (REDUCE) { el.textContent = target + suffix; return; }

      var dur = 900, t0 = 0;
      function frame(ts) {
        if (!t0) t0 = ts;
        var p = Math.min(1, (ts - t0) / dur);
        var e = 1 - Math.pow(1 - p, 3);   // 三次 ease-out：起步快，读数一下就窜上来
        el.textContent = Math.round(target * e) + suffix;
        if (p < 1) window.requestAnimationFrame(frame);
      }
      window.requestAnimationFrame(frame);
    }

    if (!('IntersectionObserver' in window)) {
      els.forEach(run); return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        run(e.target); io.unobserve(e.target);
      });
    }, { threshold: 0.4 });
    els.forEach(function (el) { io.observe(el); });
  })();

  /* ───────────────────────────────────────────────────────────
     4. 流水线逐级点亮
     ─────────────────────────────────────────────────────────── */
  (function pipeline() {
    var steps = $$('.pipe-step');
    if (!steps.length) return;
    if (REDUCE || !('IntersectionObserver' in window)) {
      steps.forEach(function (el) { el.classList.add('on'); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        // 按 DOM 里的先后顺序依次点，形成「流过去了」的感觉
        steps.forEach(function (s, i) {
          window.setTimeout(function () { s.classList.add('on'); }, i * 130);
        });
        io.disconnect();
      });
    }, { threshold: 0.25 });
    io.observe(steps[0]);
  })();

  /* ───────────────────────────────────────────────────────────
     5. 卡片柔光跟随光标
        写 CSS 变量只影响这一个元素；不要往父容器上写，
        那是会引发整棵子树样式重算的经典错误。
     ─────────────────────────────────────────────────────────── */
  (function glow() {
    if (!window.matchMedia || !window.matchMedia('(hover:hover) and (pointer:fine)').matches) return;
    $$('.feat,.pcard2,.plan').forEach(function (card) {
      card.addEventListener('pointermove', function (ev) {
        var r = card.getBoundingClientRect();
        card.style.setProperty('--mx', (ev.clientX - r.left) + 'px');
        card.style.setProperty('--my', (ev.clientY - r.top) + 'px');
      });
    });
  })();

  /* ───────────────────────────────────────────────────────────
     6. FAQ 展开 / 收起
        高度为 0fr→1fr（见 CSS），这里只管切 class 和 aria。
        同时只允许一个开着，避免页面被撑得忽长忽短。
     ─────────────────────────────────────────────────────────── */
  (function faq() {
    $$('.qa').forEach(function (item) {
      var btn = $('.q', item);
      if (!btn) return;
      if (!btn.hasAttribute('aria-expanded')) btn.setAttribute('aria-expanded', 'false');
      btn.addEventListener('click', function () {
        var open = item.classList.toggle('open');
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        $$('.qa.open', item.parentNode).forEach(function (other) {
          if (other === item) return;
          other.classList.remove('open');
          var b = $('.q', other);
          if (b) b.setAttribute('aria-expanded', 'false');
        });
      });
    });
  })();

  /* ───────────────────────────────────────────────────────────
     7. ★ Demo Deck —— 可点的功能演示舱
        左边选参数，右边「跑」一遍。每个 tab 的结果行都是静态写在
        HTML 里的，脚本只负责把它们逐行放出来。
     ─────────────────────────────────────────────────────────── */
  (function deck() {
    var root = $('#deck');
    if (!root) return;

    var tabs = $$('.deck-tab', root);
    var panes = $$('.deck-pane', root);
    var outs = $$('.deck-out', root);
    var rowsWrap = $('.deck-rows', root);
    var stat = $('.deck-stat', root);
    var meterFill = $('.deck-meter .mf', root);
    var meterTxt = $('.deck-meter .mt span:last-child', root);
    var runBtn = $('.deck-run', root);
    var timer = null;

    function show(name) {
      tabs.forEach(function (t) {
        t.setAttribute('aria-selected', t.getAttribute('data-tab') === name ? 'true' : 'false');
      });
      var pane = null;
      panes.forEach(function (p) {
        var on = p.getAttribute('data-pane') === name;
        p.hidden = !on;
        if (on) pane = p;
      });
      outs.forEach(function (o) {
        o.hidden = o.getAttribute('data-out') !== name;
      });
      // 换过来的这块给一次短促的横移，告诉用户「内容确实换了」
      if (pane && !REDUCE) {
        pane.style.animation = 'none';
        void pane.offsetWidth;
        pane.style.animation = '';
      }
      reset();
    }

    // 复位到「没跑过」的样子：进度条归零、按钮恢复文案。
    // 结果行不在这里处理 —— 它们由下一次 play() 负责重启动画。
    function reset() {
      window.clearTimeout(timer);
      if (meterFill) meterFill.style.width = '0%';
      if (meterTxt) meterTxt.textContent = '等待开始';
      if (stat) stat.textContent = '就绪';
      if (runBtn) {
        runBtn.disabled = false;
        runBtn.textContent = runBtn.getAttribute('data-run') || '▶ 运行看看';
      }
    }

    // 重启一行元素的动画：先摘掉 animation，强迫一次重排再挂回去。
    // 这是 CSS 动画里「想重新演一遍」的标准做法，没有别的招。
    function replay(rows) {
      rows.forEach(function (r, i) {
        r.style.animation = 'none';
        r.style.animationDelay = '';
        void r.offsetWidth;
        r.style.animation = '';
        r.style.animationDelay = (i * 42) + 'ms';   // 42ms 步进，落在 30~80ms 区间
      });
    }

    function play() {
      if (!rowsWrap) return;
      // 结果行按 tab 分组，各自装在 .deck-out 里，隐藏的那些不参与
      var rows = $$('.deck-out:not([hidden]) .deck-row', rowsWrap);
      if (!rows.length) return;

      window.clearTimeout(timer);
      if (runBtn) {
        runBtn.disabled = true;
        runBtn.textContent = '运行中…';
      }
      if (stat) stat.textContent = '运行中';
      if (meterTxt) meterTxt.textContent = '运行中';

      replay(rows);

      if (REDUCE) { done(); return; }
      timer = window.setTimeout(done, rows.length * 42 + 420);
    }

    function done() {
      if (meterFill) meterFill.style.width = '100%';
      if (meterTxt) meterTxt.textContent = '完成';
      if (stat) stat.textContent = '完成';
      if (runBtn) {
        runBtn.disabled = false;
        runBtn.textContent = '↻ 再跑一次';
      }
    }

    tabs.forEach(function (t) {
      t.addEventListener('click', function () { show(t.getAttribute('data-tab')); });
    });
    outs.forEach(function (o) {
      if (!o.hidden) {
        // 初始 tab 的结果行直接可见，不闪
        $$('.deck-row', o).forEach(function (r, i) { r.style.animationDelay = (i * 42) + 'ms'; });
      }
    });
    if (runBtn) runBtn.addEventListener('click', play);

    // 演示舱滚进视口时自动跑一遍 —— 这是这块唯一「自动」的东西，
    // 一天也就触发一两次，值得给。
    if (!REDUCE && 'IntersectionObserver' in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (!e.isIntersecting) return;
          io.disconnect();
          window.setTimeout(play, 260);
        });
      }, { threshold: 0.35 });
      io.observe(root);
    }

    // 选项 chip：纯视觉反馈，不接后端
    $$('.deck-opt', root).forEach(function (opt) {
      opt.addEventListener('click', function () {
        $$('.deck-opt', opt.parentNode).forEach(function (o) { o.classList.remove('on'); });
        opt.classList.add('on');
      });
    });

    show((tabs[0] && tabs[0].getAttribute('data-tab')) || 'dl');
  })();

  /* ───────────────────────────────────────────────────────────
     8. 命令面板（Ctrl/⌘ + K）
        列表内容从页面上真实存在的导航链接和带 id 的区块自动收集，
        所以任何页面挂上这段脚本都有得跳，不需要另外维护一份清单。
     ─────────────────────────────────────────────────────────── */
  (function command() {
    if ($('#cmd')) return;
    var items = [];
    function collect() {
      var seen = {};
      function add(label, href, group) {
        if (!href || seen[href + label]) return;
        seen[href + label] = 1;
        items.push({ label: label, href: href, group: group });
      }
      $$('.nav-links a, .mnav a').forEach(function (a) {
        add(a.textContent.trim(), a.getAttribute('href'), '页面');
      });
      $$('section[id]').forEach(function (s) {
        var h = $('h2', s);
        if (h) add(h.textContent.trim(), '#' + s.id, '本页');
      });
      var dl = $('a[href$=".exe"]');
      if (dl) add('下载 Windows 安装包', dl.getAttribute('href'), '动作');
    }

    var box = doc.createElement('div');
    box.className = 'cmd';
    box.id = 'cmd';
    box.innerHTML =
      '<div class="cmd-box" role="dialog" aria-modal="true" aria-label="站内跳转">' +
        '<div class="cmd-in">' +
          '<input id="cmdInput" type="text" placeholder="跳到功能、页面或下载…" autocomplete="off">' +
          '<span class="cmd-kbd">ESC 关闭</span>' +
        '</div>' +
        '<div class="cmd-list" id="cmdList"></div>' +
        '<div class="cmd-foot"><span>↑↓ 选择</span><span>⏎ 打开</span><span>ESC 关闭</span></div>' +
      '</div>';
    doc.body.appendChild(box);

    var input = $('#cmdInput', box);
    var list = $('#cmdList', box);
    var cur = 0;
    var filtered = [];

    function render(q) {
      filtered = items.filter(function (it) {
        return !q || it.label.toLowerCase().indexOf(q.toLowerCase()) >= 0;
      });
      list.innerHTML = '';
      if (!filtered.length) {
        var emp = doc.createElement('div');
        emp.className = 'cmd-empty';
        emp.textContent = '没找到，换个词试试';
        list.appendChild(emp);
        return;
      }
      if (cur >= filtered.length) cur = 0;
      filtered.forEach(function (it, i) {
        var b = doc.createElement('button');
        b.className = 'cmd-item' + (i === cur ? ' sel' : '');
        b.type = 'button';
        // 标签用 textContent 写，不要拼 HTML 字符串
        var label = doc.createElement('span');
        label.textContent = it.label;
        b.appendChild(label);
        var g = doc.createElement('span');
        g.className = 'cg';
        g.textContent = it.group;
        b.appendChild(g);
        b.addEventListener('click', function () { go(it.href); });
        list.appendChild(b);
      });
    }

    function go(href) {
      close();
      if (href.charAt(0) === '#') {
        var el = doc.getElementById(href.slice(1));
        if (el) {
          var top = el.getBoundingClientRect().top + window.pageYOffset - 80;
          window.scrollTo({ top: top, behavior: REDUCE ? 'auto' : 'smooth' });
        }
      } else {
        window.location.href = href;
      }
    }

    function move(d) {
      if (!filtered.length) return;
      cur = (cur + d + filtered.length) % filtered.length;
      render(input.value);
      var el = $('.cmd-item.sel', list);
      if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    }

    function open() {
      collect();
      if (!items.length) return;
      cur = 0;
      render('');
      box.classList.add('show');
      window.setTimeout(function () { input.focus(); }, 20);
    }
    function close() { box.classList.remove('show'); }

    input.addEventListener('input', function () { cur = 0; render(input.value); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Enter') {
        e.preventDefault();
        if (filtered[cur]) go(filtered[cur].href);
      } else if (e.key === 'Escape') { close(); }
    });
    box.addEventListener('click', function (e) { if (e.target === box) close(); });

    doc.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        box.classList.contains('show') ? close() : open();
      } else if (e.key === 'Escape' && box.classList.contains('show')) {
        close();
      } else if (e.key === '/' && doc.activeElement === doc.body) {
        e.preventDefault(); open();
      }
    });

    // 角标：宽屏才出现，手机上会被内容挡住，反而吵
    if (window.matchMedia && window.matchMedia('(min-width:901px)').matches) {
      var trig = doc.createElement('button');
      trig.className = 'cmd-trigger';
      trig.type = 'button';
      trig.textContent = '⌘K 跳功能';
      trig.addEventListener('click', open);
      doc.body.appendChild(trig);
    }
  })();

  /* ───────────────────────────────────────────────────────────
     9. 复制按钮：点完给个明确的「成了」
     ─────────────────────────────────────────────────────────── */
  (function copy() {
    $$('[data-copy]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var txt = btn.getAttribute('data-copy') || '';
        var old = btn.textContent;
        function ok() {
          btn.textContent = '✓ 已复制';
          btn.classList.add('done');
          window.setTimeout(function () {
            btn.textContent = old; btn.classList.remove('done');
          }, 1600);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(txt).then(ok, function () { ok(); });
        } else {
          var ta = doc.createElement('textarea');
          ta.value = txt; doc.body.appendChild(ta); ta.select();
          try { doc.execCommand('copy'); ok(); } catch (err) { /* 不给就算了 */ }
          doc.body.removeChild(ta);
        }
      });
    });
  })();

  /* ───────────────────────────────────────────────────────────
     10. 导航高亮当前区块
     ─────────────────────────────────────────────────────────── */
  (function current() {
    var links = $$('.nav-links a[href^="#"]');
    var map = {};
    links.forEach(function (a) {
      var el = doc.getElementById(a.getAttribute('href').slice(1));
      if (el) map[el.id] = a;
    });
    var ids = Object.keys(map);
    if (!ids.length || !('IntersectionObserver' in window)) return;

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        var a = map[e.target.id];
        if (a) a.classList.toggle('on', e.isIntersecting);
      });
    }, { rootMargin: '-45% 0px -50% 0px' });
    ids.forEach(function (id) { io.observe(doc.getElementById(id)); });
  })();

  /* ───────────────────────────────────────────────────────────
     11. 左侧滚动刻度导轨
        一把自绘的尺子：中性刻度 + 白色游标 + 当前章节名。
        两条硬约束：
        ① 位移必须 1:1 跟手 —— 任何加在游标位移上的过渡都会发黏，
           所以「带过渡的形变」一律拆到内层 <i> 上去做。
        ② 脚本没跑时原生滚动条必须还在，所以 has-rail 由这里加，
           而不是写死在 HTML 里。
     ─────────────────────────────────────────────────────────── */
  (function scrollRail() {
    var rail = $('#rail');
    if (!rail || !$('#railThumb')) return;

    var thumb = $('#railThumb');
    var label = $('#railLabel');
    var mags = $$('.rail-mag i', rail);
    var docEl = doc.documentElement;

    var H = thumb.offsetHeight || 20;   // 游标高：读实际值，以后改 CSS 不用改 JS
    var TICK = 10;                      // 刻度周期，必须与 CSS 渐变里的 10px 一致
    var MAG_K = [-3, -2, -1, 0, 1, 2, 3];
    var MAG_S = [0.30, 0.46, 0.66, 1.00, 0.66, 0.46, 0.30];  // 宽度倍率
    var MAG_T = [0.25, 0.50, 0.75, 1.00, 0.75, 0.50, 0.25];  // 厚度倍率

    var secs = $$('section[data-rail]');
    var spans = [];
    var curEl = null;
    var labelH = 30;
    var lastY = 0;
    var idle = 0;
    var pending = false;

    function measure() {
      spans = secs.map(function (s) {
        return { el: s, top: s.offsetTop, bottom: s.offsetTop + s.offsetHeight };
      });
    }

    // 当前段落：用「视口上三分之一处的一条横线」判定。
    // 不用 IntersectionObserver —— 段交界处会同时命中两段，名字会跳错。
    function syncCurrent() {
      var probeY = (window.pageYOffset || 0) + window.innerHeight * 0.33;
      var hit = null;
      for (var i = 0; i < spans.length; i++) {
        if (spans[i].top <= probeY && spans[i].bottom > probeY) { hit = spans[i]; break; }
      }
      var el = hit ? hit.el : null;
      if (el === curEl) return;

      if (curEl) curEl.classList.remove('is-current');
      curEl = el;
      if (!curEl) {
        label.firstElementChild.textContent = '';
        label.lastElementChild.textContent = '';
        label.classList.remove('is-swap');
        return;
      }
      curEl.classList.add('is-current');
      var no = secs.indexOf(curEl) + 1;
      label.firstElementChild.textContent = no < 10 ? '0' + no : String(no);
      label.lastElementChild.textContent = curEl.getAttribute('data-rail') || '';
      labelH = label.offsetHeight || 30;   // 高度只有换章时才变，别每帧读
      label.classList.remove('is-swap');
      void label.offsetWidth;              // 强制重排，让入场动画能重播
      label.classList.add('is-swap');
    }

    function update() {
      var railH = rail.clientHeight;
      var max = docEl.scrollHeight - window.innerHeight;
      var y0 = window.pageYOffset || docEl.scrollTop || 0;

      if (max <= 4 || railH <= 0) { rail.hidden = true; return; }
      rail.hidden = false;

      var p = Math.min(1, Math.max(0, y0 / max));
      var y = (railH - H) * p;      // 游标顶
      var yc = y + H / 2;           // 游标中心

      thumb.style.transform = 'translate3d(0,' + y + 'px,0)';
      label.style.transform = 'translate3d(0,' + (yc - labelH / 2) + 'px,0)';

      // 速度感应：一帧跨过 36px 就算快滚 → 内层纵向拉长，停下自然回弹
      rail.classList.toggle('is-fast', Math.abs(y0 - lastY) > 36);
      rail.classList.toggle('at-top', y0 < 8);
      rail.classList.toggle('at-bottom', y0 > max - 8);
      lastY = y0;

      // 放大镜：以「离游标最近的那条刻度线」为基准排 7 条。
      // ★ 相位必须和底层渐变对齐（都从 top:0 起、每 TICK 一条），
      //   否则放大条会和底纹错开半个周期，看起来像双线。
      var base = Math.round(yc / TICK) * TICK;
      for (var i = 0; i < mags.length; i++) {
        var ty = base + MAG_K[i] * TICK;
        if (ty < 0 || ty > railH) { mags[i].style.opacity = '0'; continue; }
        mags[i].style.opacity = '';
        mags[i].style.transform =
          'translate3d(0,' + ty + 'px,0) scale(' + MAG_S[i] + ',' + MAG_T[i] + ')';
      }

      syncCurrent();
    }

    // ★ 只靠 rAF 会卡死：标签页在后台时浏览器会挂起渲染帧，
    //   若用「!raf 就调度」这种锁，raf 会一直非 0，回到前台后滚多少都不再更新。
    //   所以 rAF 与 120ms 超时双保险，谁先到谁更新，pending 保证只跑一次。
    function schedule() {
      if (pending) return;
      pending = true;
      var run = function () { if (!pending) return; pending = false; update(); };
      window.requestAnimationFrame(run);
      window.setTimeout(run, 120);
    }

    function onScroll() {
      schedule();
      rail.classList.add('is-active');
      window.clearTimeout(idle);
      idle = window.setTimeout(function () {
        rail.classList.remove('is-active');
      }, 700);
    }

    // 顺序很重要：先让 JS 接管，再隐藏原生滚动条
    docEl.classList.add('has-rail');
    measure();
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', function () { measure(); update(); }, { passive: true });
    window.addEventListener('load', function () { measure(); update(); });
    update();

    // 图片、字体、折叠块撑高页面后高度会变，得重算
    if ('ResizeObserver' in window) {
      new ResizeObserver(function () { measure(); update(); }).observe(doc.body);
    }
  })();

})();
