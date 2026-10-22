import { useEffect, useRef } from 'react'

type Particle = {
  x: number
  y: number
  vx: number
  vy: number
  size: number
  life: number
  decay: number
  color: string
}

const PARTICLE_COLORS = ['111, 151, 141', '137, 137, 169', '105, 139, 151']
const MAX_PARTICLES = 28

/** 仅在指针移动期间工作的轻量粒子层；空闲、触屏或减少动态时不占用动画帧。 */
export function MouseParticles() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const finePointer = window.matchMedia('(pointer: fine)').matches
    if (!canvas || reducedMotion || !finePointer) return

    const context = canvas.getContext('2d', { alpha: true })
    if (!context) return

    let particles: Particle[] = []
    let animationFrame = 0
    let previousFrame = 0
    let previousPointerX = 0
    let previousPointerY = 0
    let previousSpawn = 0
    let hasPointer = false

    const resize = () => {
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5)
      canvas.width = Math.round(window.innerWidth * ratio)
      canvas.height = Math.round(window.innerHeight * ratio)
      context.setTransform(ratio, 0, 0, ratio, 0, 0)
    }

    const draw = (now: number) => {
      animationFrame = 0
      const delta = previousFrame ? Math.min((now - previousFrame) / 16.67, 2) : 1
      previousFrame = now
      context.clearRect(0, 0, window.innerWidth, window.innerHeight)

      particles = particles.filter((particle) => {
        particle.life -= particle.decay * delta
        if (particle.life <= 0) return false

        particle.x += particle.vx * delta
        particle.y += particle.vy * delta
        particle.vx *= 0.965
        particle.vy = particle.vy * 0.965 - 0.012 * delta

        const opacity = particle.life * particle.life * 0.62
        context.beginPath()
        context.arc(particle.x, particle.y, particle.size * (0.65 + particle.life * 0.35), 0, Math.PI * 2)
        context.fillStyle = `rgba(${particle.color}, ${opacity})`
        context.fill()
        return true
      })

      if (particles.length > 0 && !document.hidden) {
        animationFrame = window.requestAnimationFrame(draw)
      } else {
        previousFrame = 0
      }
    }

    const wake = () => {
      if (!animationFrame && !document.hidden) animationFrame = window.requestAnimationFrame(draw)
    }

    const followPointer = (event: PointerEvent) => {
      if (event.pointerType === 'touch') return
      const now = performance.now()
      const dx = hasPointer ? event.clientX - previousPointerX : 0
      const dy = hasPointer ? event.clientY - previousPointerY : 0
      const distance = Math.hypot(dx, dy)
      if (hasPointer && now - previousSpawn < 18 && distance < 7) return

      const count = Math.min(distance > 18 ? 2 : 1, MAX_PARTICLES - particles.length)
      for (let index = 0; index < count; index += 1) {
        const spread = (Math.random() - 0.5) * 7
        particles.push({
          x: event.clientX + spread,
          y: event.clientY + (Math.random() - 0.5) * 7,
          vx: -dx * 0.025 + (Math.random() - 0.5) * 0.7,
          vy: -dy * 0.025 + (Math.random() - 0.5) * 0.7,
          size: 1.4 + Math.random() * 2.3,
          life: 1,
          decay: 0.022 + Math.random() * 0.014,
          color: PARTICLE_COLORS[Math.floor(Math.random() * PARTICLE_COLORS.length)],
        })
      }

      previousPointerX = event.clientX
      previousPointerY = event.clientY
      previousSpawn = now
      hasPointer = true
      wake()
    }

    const onVisibilityChange = () => {
      if (document.hidden) {
        window.cancelAnimationFrame(animationFrame)
        animationFrame = 0
        previousFrame = 0
        particles = []
        context.clearRect(0, 0, window.innerWidth, window.innerHeight)
      }
    }

    resize()
    window.addEventListener('resize', resize, { passive: true })
    window.addEventListener('pointermove', followPointer, { passive: true })
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      window.cancelAnimationFrame(animationFrame)
      window.removeEventListener('resize', resize)
      window.removeEventListener('pointermove', followPointer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [])

  return <canvas ref={canvasRef} className="mouse-particles" aria-hidden="true" />
}
