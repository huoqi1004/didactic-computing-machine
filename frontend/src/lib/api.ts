import axios from 'axios'

const api = axios.create({ baseURL: '/api/v1' })
let _token = sessionStorage.getItem('erp-token') || ''

api.interceptors.request.use(cfg => {
  if (_token) cfg.headers.Authorization = `Bearer ${_token}`
  return cfg
})

api.interceptors.response.use(
  r => r.data,
  async err => {
    if (err.response?.status === 401 && !err.config._retry) {
      _token = ''; sessionStorage.removeItem('erp-token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  })

export function setToken(t: string) { _token = t; sessionStorage.setItem('erp-token', t) }
export function clearToken() { _token = ''; sessionStorage.removeItem('erp-token') }
export function getToken() { return _token }
export default api
