import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth, type UserRole } from './store/auth'
import { SessionBar } from './components/SessionBar'
import { LoginPage } from './pages/LoginPage'
import { DataSourcePage } from './pages/DataSourcePage'
import { ScopePage } from './pages/ScopePage'
import { PolicyPage } from './pages/PolicyPage'
import { ConsolePage } from './pages/ConsolePage'
import { ReportPage } from './pages/ReportPage'
import { SmartDoctorPage } from './pages/SmartDoctorPage'
import { UserAdminPage } from './pages/UserAdminPage'
import './design.css'

const ALL: UserRole[] = ['admin', 'staff', 'patient']

interface NavItem {
  to: string
  label: string
  roles: UserRole[]
}

/**
 * 导航按角色过滤。
 *
 * 医护与病患的导航**故意相同**——两者的差别在于令牌能查到什么数据，
 * 不在于能进哪些页面。这正是演示要证明的：同一套界面、同一个问题，
 * 两类令牌给出不同结果。
 *
 * 患者多一个「智慧医生」入口：面向患者的可信就医助手（智慧医生.docx），
 * 且它是患者登录后的默认页。管理员多出「数据源」「安全策略」「用户管理」：
 * 那是平台配置，不该开放给临床用户与病患。
 */
function navItems(role: UserRole): NavItem[] {
  const items: NavItem[] = [
    {
      to: '/',
      label: role === 'admin' ? '数据源' : role === 'patient' ? '智慧医生' : '可查范围',
      roles: ALL,
    },
    { to: '/scope', label: '可查范围', roles: ['patient'] },
    { to: '/policy', label: '安全策略', roles: ['admin'] },
    { to: '/users', label: '用户管理', roles: ['admin'] },
    { to: '/console', label: '查询控制台', roles: ALL },
    { to: '/reports', label: '安全报告', roles: ALL },
  ]
  return items.filter((i) => i.roles.includes(role))
}

function Shell() {
  const { session } = useAuth()

  // 未登录：只渲染登录页，不挂载应用外壳。
  if (!session) return <LoginPage />

  const items = navItems(session.role)
  const isAdmin = session.role === 'admin'

  return (
    <div className="app">
      {/* 键盘用户按 Tab 的第一站：跳过页眉与导航，直达内容 */}
      <a className="skip-link" href="#main">
        跳到主要内容
      </a>

      <header className="app-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">盾</span>
          <h1>医患信息数据服务云平台</h1>
          <span className="engine-tag">安全引擎 · 医盾</span>
        </div>
        <SessionBar />
      </header>

      <nav className="app-nav">
        {items.map((i) => (
          <NavLink key={i.to} to={i.to} end={i.to === '/'}>
            {i.label}
          </NavLink>
        ))}
      </nav>

      <main className="app-main" id="main">
        <Routes>
          {/* 同一个入口，三种视图：管理员看库表结构，医护看业务化的
              「可查范围」，患者默认落「智慧医生」（docx 要求）。 */}
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
              这里是第二层，第一层在接口上（非管理员令牌建号返回 403）。 */}
          <Route
            path="/users"
            element={isAdmin ? <UserAdminPage /> : <Forbidden />}
          />
          <Route path="/console" element={<ConsolePage />} />
          <Route path="/reports" element={<ReportPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}

/** 未知地址。
 *
 * 与「无权访问」是两回事：那个是权限问题（页面存在但你进不去），
 * 这个是地址本身不存在。混为一谈会让用户以为是权限被收走了，
 * 然后去找管理员——排查方向完全是错的。
 */
function NotFound() {
  return (
    <div className="page">
      <h2>页面不存在</h2>
      <p className="hint">
        地址可能有误，或该页面已被移除。请从上方导航选择要前往的模块。
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
