import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { TokenProvider } from './store/token'
import { TokenSwitcher } from './components/TokenSwitcher'
import { DataSourcePage } from './pages/DataSourcePage'
import { PolicyPage } from './pages/PolicyPage'
import { ConsolePage } from './pages/ConsolePage'
import { ReportPage } from './pages/ReportPage'
import './App.css'

export default function App() {
  return (
    <TokenProvider>
      <BrowserRouter>
        <div className="app">
          <header className="app-header">
            <div className="brand">
              <h1>医患信息数据服务云平台</h1>
              <span className="engine-tag">安全引擎 · 医盾</span>
            </div>
            <TokenSwitcher />
          </header>

          <nav className="app-nav">
            <NavLink to="/">数据源</NavLink>
            <NavLink to="/policy">安全策略</NavLink>
            <NavLink to="/console">查询控制台</NavLink>
            <NavLink to="/reports">安全报告</NavLink>
          </nav>

          <main className="app-main">
            <Routes>
              <Route path="/" element={<DataSourcePage />} />
              <Route path="/policy" element={<PolicyPage />} />
              <Route path="/console" element={<ConsolePage />} />
              <Route path="/reports" element={<ReportPage />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </TokenProvider>
  )
}
