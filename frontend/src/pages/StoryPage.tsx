/**
 * 营销首页——未登录时的 `/`。
 *
 * 版面直接复用静态稿的标记与样式（storySource.html 由 design-preview
 * 首页抽取，去掉内联脚本），交互在这里以 React effect 重接：
 * 滚动进度、分段入场、磁吸描边、章节轨道、数据柱读数、WebGL2 极光与微尘。
 * 登录入口仍走 React Router，不整页刷新。
 */
import { useEffect, useRef, type MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import storySource from './storySource.html?raw'

const VERTSrc = `#version 300 es
in vec2 p;
void main(){ gl_Position = vec4(p, 0.0, 1.0); }`

const FRAGSrc = `#version 300 es
precision highp float;
out vec4 o;
uniform vec2 uRes;
uniform float uTime;
uniform vec2 uMouse;
uniform float uScroll;

float hash(vec2 p){ return fract(sin(dot(p, vec2(41.3, 289.1))) * 43758.5453); }
float noise(vec2 p){
  vec2 i = floor(p), f = fract(p);
  vec2 u = f*f*(3.0-2.0*f);
  return mix(mix(hash(i), hash(i+vec2(1,0)), u.x),
             mix(hash(i+vec2(0,1)), hash(i+vec2(1,1)), u.x), u.y);
}
float fbm(vec2 p){
  float v = 0.0, a = 0.5;
  for(int i=0;i<6;i++){ v += a*noise(p); p *= 2.02; a *= 0.5; }
  return v;
}
void main(){
  vec2 uv = (gl_FragCoord.xy - 0.5*uRes) / uRes.y;
  float t = uTime * 0.036;
  vec2 q = vec2(fbm(uv*1.32 + vec2(0.0, t)), fbm(uv*1.32 + vec2(5.2, -t*0.82)));
  vec2 r = vec2(fbm(uv*2.0 + q*1.22 + vec2(1.7,9.2) + t*0.66),
                fbm(uv*2.0 + q*1.22 + vec2(8.3,2.8) - t*0.43));
  float f = fbm(uv*2.28 + r);

  vec3 paper = vec3(0.930, 0.942, 0.920);
  vec3 sage  = vec3(0.700, 0.765, 0.728);
  vec3 moss  = vec3(0.455, 0.545, 0.510);
  vec3 sand  = vec3(0.845, 0.775, 0.640);

  float veil = smoothstep(0.15, 0.96, f);
  float ridge = smoothstep(0.43, 0.94, length(r) - 0.18);
  vec3 col = mix(paper, sage, veil*0.62);
  col = mix(col, moss, ridge*0.17);
  col = mix(col, sand, pow(clamp(q.y, 0.0, 1.0), 3.1)*0.18);

  float strata = smoothstep(0.49, 0.50, abs(sin((uv.x+uv.y)*23.0 + f*8.0)));
  col -= strata * 0.013;

  vec2 m = (uMouse - 0.5) * vec2(uRes.x/uRes.y, 1.0);
  float md = length(uv - m);
  col += vec3(0.014, 0.021, 0.017) * smoothstep(0.95, 0.0, md) * 0.7;

  float edge = smoothstep(1.12, 0.18, length(uv*vec2(0.72, 1.0)));
  col = mix(paper*0.985, col, 0.87 + 0.13*edge);
  col = mix(col, paper, uScroll*0.24);

  float grain = fract(sin(dot(gl_FragCoord.xy + uTime, vec2(12.9898,78.233))) * 43758.5453);
  col += (grain - 0.5) * 0.012;
  o = vec4(col, 1.0);
}`

export function StoryPage() {
  const rootRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  useEffect(() => {
    document.title = 'MedGuard 医盾 · 可信医疗信息入口'
  }, [])

  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    if (!(event.target instanceof Element)) return
    const link = event.target.closest('a')
    if (!link) return
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    if (link.target && link.target !== '_self') return

    const href = link.getAttribute('href') ?? ''
    if (href === '/login') {
      event.preventDefault()
      const veil = document.getElementById('pageVeil')
      const label = veil?.querySelector('.veil-label span')
      if (label) label.textContent = link.dataset.transitionLabel || '正在进入登录页'
      if (veil) {
        veil.style.setProperty('--veil-x', `${event.clientX}px`)
        veil.style.setProperty('--veil-y', `${event.clientY}px`)
        veil.classList.add('is-active')
      }
      window.setTimeout(() => {
        veil?.classList.remove('is-active')
        navigate('/login')
      }, 520)
      return
    }

    if (href.startsWith('#') && href.length > 1) {
      const target = document.getElementById(href.slice(1))
      if (!target) return
      event.preventDefault()
      scrollToId(href.slice(1))
      history.replaceState(null, '', href)
    }
  }

  useEffect(() => {
    const root = rootRef.current
    if (!root) return
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const fine = window.matchMedia('(pointer: fine)').matches
    const cleanups: Array<() => void> = []
    let paused = false
    let wakeGL: (() => void) | null = null
    let wakeMotes: (() => void) | null = null

    /* ── 滚动进度 ───────────────────────────────── */
    const setScrollProgress = () => {
      const max = Math.max(1, document.documentElement.scrollHeight - window.innerHeight)
      document.documentElement.style.setProperty(
        '--page-scroll',
        Math.min(1, window.scrollY / max).toFixed(4),
      )
    }
    let scrollTicking = false
    const requestScroll = () => {
      if (scrollTicking) return
      scrollTicking = true
      requestAnimationFrame(() => {
        setScrollProgress()
        scrollTicking = false
      })
    }
    window.addEventListener('scroll', requestScroll, { passive: true })
    window.addEventListener('resize', requestScroll)
    setScrollProgress()
    cleanups.push(() => {
      window.removeEventListener('scroll', requestScroll)
      window.removeEventListener('resize', requestScroll)
      document.documentElement.style.removeProperty('--page-scroll')
    })

    /* ── 分段入场 ───────────────────────────────── */
    const revealTargets = Array.from(root.querySelectorAll<HTMLElement>('[data-reveal]'))
    let revealObserver: IntersectionObserver | null = null
    if (reduce || !('IntersectionObserver' in window)) {
      revealTargets.forEach((el) => el.classList.add('is-visible'))
    } else {
      revealObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return
          entry.target.classList.add('is-visible')
          revealObserver?.unobserve(entry.target)
        })
      }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' })
      revealTargets.forEach((el) => revealObserver?.observe(el))
    }
    cleanups.push(() => revealObserver?.disconnect())

    /* ── 鼠标磁吸描边 ───────────────────────────── */
    if (fine) {
      const SELECTOR = 'a, button, [role="button"], summary'
      let magnet: Element | null = null
      const release = () => {
        if (!magnet) return
        magnet.classList.remove('is-magnetic')
        magnet = null
      }
      const over = (event: PointerEvent) => {
        if (!(event.target instanceof Element)) return
        const next = event.target.closest(SELECTOR)
        if (next === magnet) return
        release()
        if (!next) return
        next.classList.add('is-magnetic')
        magnet = next
      }
      const out = (event: PointerEvent) => {
        if (!magnet) return
        if (event.relatedTarget instanceof Node && magnet.contains(event.relatedTarget)) return
        release()
      }
      document.addEventListener('pointerover', over, { passive: true })
      document.addEventListener('pointerout', out, { passive: true })
      window.addEventListener('blur', release)
      cleanups.push(() => {
        document.removeEventListener('pointerover', over)
        document.removeEventListener('pointerout', out)
        window.removeEventListener('blur', release)
        release()
      })
    }

    /* ── 顶栏与章节轨道 ─────────────────────────── */
    const nav = root.querySelector<HTMLElement>('#siteNav')
    const updateNav = () => nav?.classList.toggle('is-scrolled', window.scrollY > 26)
    updateNav()
    window.addEventListener('scroll', updateNav, { passive: true })
    cleanups.push(() => window.removeEventListener('scroll', updateNav))

    const chapters = Array.from(root.querySelectorAll<HTMLElement>('[data-chapter]'))
    const chapterLinks = Array.from(root.querySelectorAll<HTMLElement>('[data-chapter-link]'))
    let spy: IntersectionObserver | null = null
    if ('IntersectionObserver' in window) {
      spy = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return
          const id = entry.target.getAttribute('data-chapter')
          chapterLinks.forEach((link) => {
            if (link.dataset.chapterLink === id) link.setAttribute('aria-current', 'true')
            else link.removeAttribute('aria-current')
          })
        })
      }, { threshold: 0.25, rootMargin: '-18% 0px -50% 0px' })
      chapters.forEach((chapter) => spy?.observe(chapter))
    }
    cleanups.push(() => spy?.disconnect())

    /* ── 数据柱：视差与读数 ─────────────────────── */
    const stage = root.querySelector<HTMLElement>('#dataStage')
    const columns = root.querySelector<HTMLElement>('#dataColumns')
    if (stage && columns && !reduce) {
      const move = (event: PointerEvent) => {
        const rect = stage.getBoundingClientRect()
        const x = (event.clientX - rect.left) / rect.width - 0.5
        const y = (event.clientY - rect.top) / rect.height - 0.5
        columns.style.setProperty('--tilt-x', (x * 4).toFixed(2))
        columns.style.setProperty('--tilt-y', (y * 2).toFixed(2))
      }
      const leave = () => {
        columns.style.setProperty('--tilt-x', '0')
        columns.style.setProperty('--tilt-y', '0')
      }
      stage.addEventListener('pointermove', move, { passive: true })
      stage.addEventListener('pointerleave', leave)
      cleanups.push(() => {
        stage.removeEventListener('pointermove', move)
        stage.removeEventListener('pointerleave', leave)
      })
    }

    const readoutTitle = root.querySelector<HTMLElement>('#datasetReadoutTitle')
    const readout = root.querySelector<HTMLElement>('#datasetReadout')
    root.querySelectorAll<HTMLElement>('.data-col').forEach((button) => {
      const show = () => {
        const name = button.querySelector('.data-name')?.textContent ?? ''
        if (readoutTitle) readoutTitle.textContent = name
        if (readout) readout.textContent = button.dataset.note || ''
      }
      button.addEventListener('pointerenter', show)
      button.addEventListener('focus', show)
      button.addEventListener('click', show)
      cleanups.push(() => {
        button.removeEventListener('pointerenter', show)
        button.removeEventListener('focus', show)
        button.removeEventListener('click', show)
      })
    })

    const board = root.querySelector<HTMLElement>('.data-board')
    let boardObserver: IntersectionObserver | null = null
    if (board && 'IntersectionObserver' in window) {
      boardObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return
          board.classList.add('is-visible')
          boardObserver?.disconnect()
        })
      }, { threshold: 0.18 })
      boardObserver.observe(board)
    }
    cleanups.push(() => boardObserver?.disconnect())

    /* ── WebGL2 极光场 ──────────────────────────── */
    const canvas = root.querySelector<HTMLCanvasElement>('#gl')
    const statusEl = root.querySelector<HTMLElement>('#gpuStatus')
    let gl: WebGL2RenderingContext | null = null
    let glFrame = 0

    if (canvas) {
      gl = canvas.getContext('webgl2', {
        antialias: true,
        alpha: false,
        powerPreference: 'high-performance',
      })
      if (!gl) {
        if (statusEl) statusEl.textContent = '静态场域 · WEBGL2 不可用'
        canvas.style.background = 'radial-gradient(circle at 70% 20%, #d8dfd4, transparent 52%), #e7ece5'
      } else {
        const context = gl
        const compile = (type: number, source: string) => {
          const shader = context.createShader(type)
          if (!shader) return null
          context.shaderSource(shader, source)
          context.compileShader(shader)
          if (!context.getShaderParameter(shader, context.COMPILE_STATUS)) {
            console.error('MedGuard shader:', context.getShaderInfoLog(shader))
            context.deleteShader(shader)
            return null
          }
          return shader
        }
        const vs = compile(context.VERTEX_SHADER, VERTSrc)
        const fs = compile(context.FRAGMENT_SHADER, FRAGSrc)
        const program = vs && fs ? context.createProgram() : null
        if (program && vs && fs) {
          context.attachShader(program, vs)
          context.attachShader(program, fs)
          context.linkProgram(program)
        }

        if (!program || !context.getProgramParameter(program, context.LINK_STATUS)) {
          if (statusEl) statusEl.textContent = '静态场域 · 着色器未启用'
          canvas.style.background = 'radial-gradient(circle at 70% 20%, #d8dfd4, transparent 52%), #e7ece5'
        } else {
          context.useProgram(program)
          const buffer = context.createBuffer()
          context.bindBuffer(context.ARRAY_BUFFER, buffer)
          context.bufferData(
            context.ARRAY_BUFFER,
            new Float32Array([-1, -1, 3, -1, -1, 3]),
            context.STATIC_DRAW,
          )
          const loc = context.getAttribLocation(program, 'p')
          context.enableVertexAttribArray(loc)
          context.vertexAttribPointer(loc, 2, context.FLOAT, false, 0, 0)

          const uRes = context.getUniformLocation(program, 'uRes')
          const uTime = context.getUniformLocation(program, 'uTime')
          const uMouse = context.getUniformLocation(program, 'uMouse')
          const uScroll = context.getUniformLocation(program, 'uScroll')

          let mx = 0.5
          let my = 0.5
          let tx = 0.5
          let ty = 0.5
          const pointer = (event: PointerEvent) => {
            tx = event.clientX / Math.max(1, window.innerWidth)
            ty = 1 - event.clientY / Math.max(1, window.innerHeight)
          }
          window.addEventListener('pointermove', pointer, { passive: true })
          cleanups.push(() => window.removeEventListener('pointermove', pointer))

          const resize = () => {
            const dpr = Math.min(window.devicePixelRatio || 1, 2)
            const w = Math.floor(canvas.clientWidth * dpr)
            const h = Math.floor(canvas.clientHeight * dpr)
            if (w === canvas.width && h === canvas.height) return
            canvas.width = w
            canvas.height = h
            context.viewport(0, 0, w, h)
          }
          window.addEventListener('resize', resize)
          cleanups.push(() => window.removeEventListener('resize', resize))
          resize()

          const start = performance.now()
          let running = false
          let frames = 0
          let last = performance.now()
          let acc = 0

          const draw = (now: number) => {
            if (paused) {
              running = false
              if (statusEl) statusEl.textContent = '动效已暂停'
              return
            }
            running = true
            resize()
            mx += (tx - mx) * 0.045
            my += (ty - my) * 0.045
            const elapsed = reduce ? 0.8 : (now - start) / 1000
            context.uniform2f(uRes, canvas.width, canvas.height)
            context.uniform1f(uTime, elapsed)
            context.uniform2f(uMouse, mx, my)
            const progress = parseFloat(
              getComputedStyle(document.documentElement).getPropertyValue('--page-scroll') || '0',
            ) || 0
            context.uniform1f(uScroll, progress)
            context.drawArrays(context.TRIANGLES, 0, 3)

            frames++
            acc += now - last
            last = now
            if (acc > 800 && statusEl) {
              statusEl.textContent = reduce
                ? '静态场域 · 已降低动效'
                : `实时生成 · WEBGL2 · ${Math.round((frames * 1000) / acc)} FPS`
              frames = 0
              acc = 0
            }
            if (!reduce) glFrame = requestAnimationFrame(draw)
            else running = false
          }

          wakeGL = () => {
            if (running || reduce) return
            last = performance.now()
            frames = 0
            acc = 0
            glFrame = requestAnimationFrame(draw)
          }
          glFrame = requestAnimationFrame(draw)
          cleanups.push(() => cancelAnimationFrame(glFrame))
        }
      }
    }

    /* ── 悬浮微尘 ───────────────────────────────── */
    const motes = root.querySelector<HTMLCanvasElement>('#motes')
    let motesFrame = 0
    if (motes) {
      const ctx = motes.getContext('2d')
      if (ctx) {
        let w = 0
        let h = 0
        let dots: Array<{ x: number; y: number; r: number; a: number; vx: number; vy: number }> = []
        const pointer = { x: -1000, y: -1000 }
        const resize = () => {
          const dpr = Math.min(window.devicePixelRatio || 1, 2)
          w = window.innerWidth
          h = window.innerHeight
          motes.width = Math.floor(w * dpr)
          motes.height = Math.floor(h * dpr)
          ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
          const count = Math.max(32, Math.min(72, Math.round((w * h) / 26000)))
          dots = Array.from({ length: count }, () => ({
            x: Math.random() * w,
            y: Math.random() * h,
            r: 0.6 + Math.random() * 1.4,
            a: 0.05 + Math.random() * 0.16,
            vx: (Math.random() - 0.5) * 0.08,
            vy: -0.04 - Math.random() * 0.1,
          }))
        }
        const move = (event: PointerEvent) => {
          pointer.x = event.clientX
          pointer.y = event.clientY
        }
        window.addEventListener('resize', resize)
        window.addEventListener('pointermove', move, { passive: true })
        resize()

        let running = false
        const draw = () => {
          if (paused) {
            running = false
            return
          }
          running = true
          ctx.clearRect(0, 0, w, h)
          for (const dot of dots) {
            if (!reduce) {
              dot.x += dot.vx
              dot.y += dot.vy
              if (dot.y < -10) { dot.y = h + 10; dot.x = Math.random() * w }
              if (dot.x < -10) dot.x = w + 10
              if (dot.x > w + 10) dot.x = -10
              const dx = dot.x - pointer.x
              const dy = dot.y - pointer.y
              const dist = Math.hypot(dx, dy)
              if (dist < 130 && dist > 0.1) {
                dot.x += (dx / dist) * 0.65
                dot.y += (dy / dist) * 0.65
              }
            }
            ctx.beginPath()
            ctx.fillStyle = `rgba(38, 58, 53, ${dot.a})`
            ctx.arc(dot.x, dot.y, dot.r, 0, Math.PI * 2)
            ctx.fill()
          }
          if (!reduce) motesFrame = requestAnimationFrame(draw)
          else running = false
        }
        wakeMotes = () => {
          if (running || reduce) return
          motesFrame = requestAnimationFrame(draw)
        }
        draw()
        cleanups.push(() => {
          cancelAnimationFrame(motesFrame)
          window.removeEventListener('resize', resize)
          window.removeEventListener('pointermove', move)
        })
      }
    }

    /* ── 背景动效暂停 ───────────────────────────── */
    const motion = root.querySelector<HTMLButtonElement>('#motionHome')
    const defaultStatus = statusEl?.textContent ?? ''
    if (motion) {
      if (reduce) {
        motion.disabled = true
        motion.setAttribute('aria-pressed', 'true')
      } else {
        const toggle = () => {
          paused = !paused
          document.documentElement.classList.toggle('motion-paused', paused)
          motion.setAttribute('aria-pressed', String(paused))
          if (statusEl) statusEl.textContent = paused ? '动效已暂停' : (defaultStatus || '实时生成 · WEBGL2')
          if (!paused) {
            wakeGL?.()
            wakeMotes?.()
          }
        }
        motion.addEventListener('click', toggle)
        cleanups.push(() => motion.removeEventListener('click', toggle))
      }
    }

    const hash = window.location.hash.slice(1)
    if (hash) requestAnimationFrame(() => scrollToId(hash))

    return () => {
      cleanups.forEach((fn) => fn())
      document.documentElement.classList.remove('motion-paused')
    }
  }, [])

  return (
    <div
      className="home story-page"
      ref={rootRef}
      onClick={onClick}
      dangerouslySetInnerHTML={{ __html: storySource }}
    />
  )
}

function scrollToId(id: string) {
  const el = document.getElementById(id)
  if (!el) return
  const nav = document.querySelector<HTMLElement>('.site-nav')
  const offset = (nav?.offsetHeight ?? 0) + 14
  const top = el.getBoundingClientRect().top + window.scrollY - offset
  window.scrollTo({ top, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
}
