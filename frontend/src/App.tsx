import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth, type UserRole } from './store/auth'
import { SessionBar } from './components/SessionBar'
import { LoginPage } from './pages/LoginPage'
import { DataSourcePage } from './pages/DataSourcePage'
import { ScopePage } from './pages/ScopePage'
import { PolicyPage } from './pages/PolicyPage'
import { ConsolePage } from './pages/ConsolePage'
import { ReportPage } from './pages/ReportPage'
import './App.css'

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
 * 管理员多出「数据源」与「安全策略」：那是平台配置，不该开放给
 * 临床用户与病患。
 */
function navItems(role: UserRole): NavItem[] {
  const items: NavItem[] = [
    { to: '/', label: role === 'admin' ? '数据源' : '可查范围', roles: ALL },
    { to: '/policy', label: '安全策略', roles: ['admin'] },
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
      <header className="app-header">
        <div className="brand">
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

      <main className="app-main">
        <Routes>
          {/* 同一个入口，两种视图：管理员看库表结构，临床用户与病患
              看业务化的「可查范围」。 */}
          <Route path="/" element={isAdmin ? <DataSourcePage /> : <ScopePage />} />
          <Route
            path="/policy"
            element={isAdmin ? <PolicyPage /> : <Forbidden />}
          />
          <Route path="/console" element={<ConsolePage />} />
          <Route path="/reports" element={<ReportPage />} />
          <Route path="*" element={<Forbidden />} />
        </Routes>
      </main>
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
