import { useEffect, useRef, useState } from 'react'
import {
  BrowserRouter,
  Navigate,
  NavLink,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom'
import { AuthProvider, useAuth, type UserRole } from './store/auth'
import { LoginPage } from './pages/LoginPage'
import { StoryPage } from './pages/StoryPage'
import { DataSourcePage } from './pages/DataSourcePage'
import { ScopePage } from './pages/ScopePage'
import { PolicyPage } from './pages/PolicyPage'
import { ConsolePage } from './pages/ConsolePage'
import { ReportPage } from './pages/ReportPage'
import { SmartDoctorPage } from './pages/SmartDoctorPage'
import { UserAdminPage } from './pages/UserAdminPage'

const ALL: UserRole[] = ['admin', 'staff', 'patient']

type IconKey = 'source' | 'scope' | 'policy' | 'users' | 'ask' | 'doctor' | 'reports'

interface NavItem {
  to: string
  label: string
  note: string
  icon: IconKey
  roles: UserRole[]
}

/**
 * 导航按角色过滤。
 *
 * 医护与病患的临床入口**故意相同**——两者的差别在于令牌能查到什么数据，
 * 不在于能进哪些页面。这正是演示要证明的：同一套界面、同一个问题，
 * 两类令牌给出不同结果。
 *
 * 患者多一个「智慧医生」入口，且它是患者登录后的默认页。管理员多出
 * 「数据源」「安全策略」「用户管理」：那是平台配置，不该开放给临床用户。
 */
function navItems(role: UserRole): NavItem[] {
  const items: NavItem[] = [
    {
      to: '/',
      label:
        role === 'admin' ? '数据源' : role === 'patient' ? '智慧医生' : '可查范围',
      note: role === 'admin' ? '库表与效能' : role === 'patient' ? '记录与解释' : '业务字段与边界',
      icon: role === 'admin' ? 'source' : role === 'patient' ? 'doctor' : 'scope',
      roles: ALL,
    },
    { to: '/scope', label: '可查范围', note: '业务字段与边界', icon: 'scope', roles: ['patient'] },
    { to: '/policy', label: '安全策略', note: '字段标注与规则', icon: 'policy', roles: ['admin'] },
    { to: '/users', label: '用户管理', note: '账号与角色', icon: 'users', roles: ['admin'] },
    { to: '/console', label: '可信问答', note: '查询控制台', icon: 'ask', roles: ALL },
    { to: '/reports', label: '安全报告', note: '审计证据', icon: 'reports', roles: ALL },
  ]
  return items.filter((i) => i.roles.includes(role))
}

const ROLE_LABEL: Record<UserRole, string> = {
  admin: '医院管理员',
  staff: '医护人员',
  patient: '病患',
}

const ROLE_BADGE: Record<UserRole, string> = {
  admin: '管',
  staff: '医',
  patient: '患',
}

const SCOPE_DESC: Record<UserRole, string> = {
  admin: '全院数据与安全策略。可维护字段标注与账号，不开放患者明细导出。',
  staff: '汇总与去标识明细。不包含全量身份信息，不开放批量导出。',
  patient: '仅本人病历与报告。可查看本人记录，不开放他人数据。',
}

function iconPath(key: IconKey) {
  switch (key) {
    case 'source':
      return <><ellipse cx="12" cy="5.5" rx="7" ry="3" /><path d="M5 5.5v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" /><path d="M5 11.5v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" /></>
    case 'scope':
      return <><path d="M4 5.5h16v13H4z" /><path d="M4 10h16M9 10v8.5" /></>
    case 'policy':
      return <><path d="M12 2.8 19 5.4v5.2c0 4.3-2.9 7.5-7 8.9-4.1-1.4-7-4.6-7-8.9V5.4l7-2.6Z" /><path d="m8.8 11.4 2 2 4.4-4.6" /></>
    case 'users':
      return <><circle cx="9" cy="8" r="3.2" /><path d="M3.8 19.5c0-2.9 2.3-5.2 5.2-5.2s5.2 2.3 5.2 5.2" /><circle cx="17" cy="9.2" r="2.4" /><path d="M16 14.6c2.4 0 4.4 1.9 4.4 4.3" /></>
    case 'ask':
      return <><path d="M4 5.5h16v11H9l-5 4v-15Z" /><path d="m8 10 2.2 2.1L15 8" /></>
    case 'doctor':
      return <><path d="M6 3v5a4 4 0 0 0 8 0V3" /><path d="M10 15v2.5a4 4 0 0 0 8 0V15" /><circle cx="18" cy="12" r="3" /><path d="M4 3h4M14 3h4" /></>
    case 'reports':
      return <><path d="M6 3h8l4 4v14H6V3Z" /><path d="M14 3v5h5M9 12h6M9 16h6" /></>
  }
}

function WorkIcon({ name }: { name: IconKey }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {iconPath(name)}
    </svg>
  )
}

function Shell() {
  const { session, signOut } = useAuth()
  const location = useLocation()
  const [sideOpen, setSideOpen] = useState(false)
  const contentRef = useRef<HTMLElement>(null)

  useEffect(() => {
    setSideOpen(false)
    contentRef.current?.scrollTo({ top: 0 })
  }, [location.pathname])

  if (!session) {
    return (
      <Routes>
        <Route path="/" element={<StoryPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  if (location.pathname === '/login') return <Navigate to="/" replace />

  const items = navItems(session.role)
  const isAdmin = session.role === 'admin'
  const current = items.find((i) => i.to === location.pathname)
  const currentTitle =
    current?.label ??
    (location.pathname === '/console' ? '可信问答' : location.pathname === '/reports' ? '安全报告' : '安全工作区')
  const avatar = ROLE_BADGE[session.role]

  return (
    <div className="console-page">
      <a className="skip-link" href="#main">跳到主要内容</a>

      <div className="app-shell">
        <div
          className={`side-scrim ${sideOpen ? 'is-on' : ''}`}
          aria-hidden="true"
          onClick={() => setSideOpen(false)}
        />

        <aside className={`app-side ${sideOpen ? 'is-open' : ''}`} id="workspace-side" aria-label="工作区导航">
          <div className="side-top">
            <NavLink className="brand" to="/" aria-label="MedGuard 医盾工作区首页">
              <span className="brand-mark" aria-hidden="true">盾</span>
              <span className="brand-words">
                <strong>MEDGUARD / 医盾</strong>
                <small>安全工作区</small>
              </span>
            </NavLink>
            <button className="side-close" type="button" aria-label="关闭导航"
                    onClick={() => setSideOpen(false)}>×</button>
          </div>

          <p className="side-section">工作区</p>
          <nav className="work-nav" aria-label="业务模块">
            {items.map((item, index) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                className="work-link"
                aria-current={location.pathname === item.to ? 'page' : undefined}
              >
                <span className="work-index">{String(index + 1).padStart(2, '0')}</span>
                <span className="work-icon"><WorkIcon name={item.icon} /></span>
                <span className="work-copy"><strong>{item.label}</strong><small>{item.note}</small></span>
              </NavLink>
            ))}
          </nav>

          <section className="scope-card" aria-labelledby="scopeLabel">
            <div className="scope-head">
              <span className="scope-label" id="scopeLabel">当前数据范围</span>
              <span className="scope-badge">只读</span>
            </div>
            <strong className="scope-name">{session.scope}</strong>
            <p className="scope-desc">{SCOPE_DESC[session.role]}</p>
          </section>

          <div className="side-foot">
            <div className="side-user">
              <span className="side-avatar" aria-hidden="true">{avatar}</span>
              <div>
                <strong>{session.displayName}</strong>
                <small>{ROLE_LABEL[session.role]} · 演示</small>
              </div>
              <button type="button" className="side-switch" onClick={() => {
                setSideOpen(false)
                signOut()
              }}>
                退出
              </button>
            </div>
            <p className="side-note">演示环境 · 全部患者记录均为虚构测试数据。</p>
          </div>
        </aside>

        <div className="app-main">
          <header className="app-top">
            <button className="menu-toggle" type="button" aria-label="打开导航"
                    aria-controls="workspace-side" aria-expanded={sideOpen}
                    onClick={() => setSideOpen(true)}>
              <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor"
                   strokeWidth="1.4" aria-hidden="true">
                <path d="M2 5h14M2 9h14M2 13h14" strokeLinecap="round" />
              </svg>
            </button>
            <div className="top-context">
              <p className="top-crumb">安全工作区</p>
              <div className="top-title-row">
                <h1>{currentTitle}</h1>
                <span className="live-status">审计在线</span>
              </div>
            </div>
            <div className="top-right">
              <div className="audit-meter" title="仅统计审计阶段，查询执行阶段仍会只读访问医院主库">
                <span className="meter-label">审计层</span><strong>0</strong> LLM <i>/</i> <strong>0</strong> DB
              </div>
              <div className="top-user">
                <span className="top-avatar" aria-hidden="true">{avatar}</span>
                <div>
                  <strong>{session.displayName}</strong>
                  <small>{ROLE_LABEL[session.role]} · 演示</small>
                </div>
              </div>
            </div>
          </header>

          <main className="app-content" id="main" tabIndex={-1} ref={contentRef}>
            <div className="route-frame" key={location.pathname}>
              <Routes>
                {/* 同一个入口，三种视图：管理员看库表结构，医护看业务化的
                    「可查范围」，患者默认落「智慧医生」。 */}
                <Route
                  path="/"
                  element={
                    isAdmin ? (
                      <DataSourcePage />
                    ) : session.role === 'patient' ? (
                      <SmartDoctorPage />
                    ) : (
                      <ScopePage />
                    )
                  }
                />
                <Route
                  path="/scope"
                  element={session.role === 'patient' ? <ScopePage /> : <Forbidden />}
                />
                <Route
                  path="/policy"
                  element={isAdmin ? <PolicyPage /> : <Forbidden />}
                />
                {/* 开号是信息科的活：患者与医护不该能自己把自己注册成医生。
                    这里是第二层，第一层在接口上。 */}
                <Route
                  path="/users"
                  element={isAdmin ? <UserAdminPage /> : <Forbidden />}
                />
                <Route path="/console" element={<ConsolePage />} />
                <Route path="/reports" element={<ReportPage />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </div>
          </main>
        </div>
      </div>
    </div>
  )
}

/** 未知地址。
 *
 * 与「无权访问」是两回事：那个是权限问题（页面存在但你进不去），
 * 这个是地址本身不存在。
 */
function NotFound() {
  return (
    <div className="page">
      <h2>页面不存在</h2>
      <p className="hint">
        地址可能有误，或该页面已被移除。请从左侧导航选择要前往的模块。
      </p>
    </div>
  )
}

/** 越权访问的落地页。
 *
 * 路由守卫是纵深防御的第二层：导航里已经不会出现该入口，但直接敲
 * 地址仍要挡住——否则「按角色门控」只是装饰。
 */
function Forbidden() {
  return (
    <div className="page">
      <h2>无权访问</h2>
      <p className="hint">
        当前身份没有查看该页面的权限。如需访问，请退出后以对应身份重新登录。
      </p>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </AuthProvider>
  )
}
