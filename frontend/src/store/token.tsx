/**
 * 令牌状态。演示用：两类身份可切换。
 * 病患令牌固定绑定 P001（对应演示库中"本人"）。
 */
import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'
import type { Token, TokenType } from '../api/types'

const STAFF_SUBJECT = null
const PATIENT_SUBJECT = 'P001'

interface TokenCtx {
  token: Token
  setTokenType: (t: TokenType) => void
}

const Ctx = createContext<TokenCtx | null>(null)

export function TokenProvider({ children }: { children: ReactNode }) {
  const [tokenType, setTokenType] = useState<TokenType>('staff')

  const value = useMemo<TokenCtx>(() => ({
    token: {
      type: tokenType,
      subject_id: tokenType === 'patient' ? PATIENT_SUBJECT : STAFF_SUBJECT,
    },
    setTokenType,
  }), [tokenType])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useToken(): TokenCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useToken 必须在 TokenProvider 内使用')
  return ctx
}
