import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertCircle, RefreshCw, Home } from 'lucide-react'

interface ErrorBoundaryProps {
  children: ReactNode
  /** 自定义 fallback 渲染函数 */
  fallback?: (error: Error, reset: () => void) => ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

/**
 * 全局错误边界
 *
 * 捕获子组件树中的 JavaScript 错误，防止整个应用白屏。
 * 显示友好的错误提示，并提供「重试」和「返回首页」按钮。
 */
export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // 输出到控制台便于调试
    console.error('[ErrorBoundary] 组件渲染错误:', error)
    console.error('[ErrorBoundary] 组件堆栈:', errorInfo.componentStack)
  }

  handleReset = (): void => {
    this.setState({ hasError: false, error: null })
  }

  handleGoHome = (): void => {
    this.setState({ hasError: false, error: null })
    window.location.href = '/'
  }

  render(): ReactNode {
    if (this.state.hasError && this.state.error) {
      // 自定义 fallback
      if (this.props.fallback) {
        return this.props.fallback(this.state.error, this.handleReset)
      }

      // 默认 fallback UI
      return (
        <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 p-8">
          <div className="w-16 h-16 rounded-full bg-status-failed/10 flex items-center justify-center">
            <AlertCircle className="w-8 h-8 text-status-failed" />
          </div>
          <div className="text-center max-w-md">
            <h2 className="text-lg font-semibold text-white mb-2">页面渲染出错</h2>
            <p className="text-sm text-slate-500 mb-1">
              页面在渲染过程中遇到错误，可以尝试重试或返回首页。
            </p>
            <p className="text-xs text-slate-600 font-mono mt-3 break-all bg-bg-input/50 rounded-lg p-3 border border-[#1e2536]">
              {this.state.error.message || '未知错误'}
            </p>
          </div>
          <div className="flex items-center gap-3 mt-2">
            <button
              onClick={this.handleReset}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-white text-sm font-medium hover:bg-primary/90 transition-colors"
            >
              <RefreshCw className="w-4 h-4" />
              重试
            </button>
            <button
              onClick={this.handleGoHome}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-bg-input border border-[#2a3142] text-slate-300 text-sm font-medium hover:text-white hover:border-primary/30 transition-colors"
            >
              <Home className="w-4 h-4" />
              返回首页
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
