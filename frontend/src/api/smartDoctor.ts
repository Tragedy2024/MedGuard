/** 智慧医生——面向患者的可信就医助手（智慧医生.docx）。 */
import { apiPost } from './client'
import type { SmartDoctorRequest, SmartDoctorResponse } from './models'

export const askSmartDoctor = (req: SmartDoctorRequest) =>
  apiPost<SmartDoctorResponse>('/api/smart-doctor/ask', req)