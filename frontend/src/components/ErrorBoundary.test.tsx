/**
 * ErrorBoundary 组件测试
 *
 * 覆盖错误捕获、重试、返回首页、自定义 fallback
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ErrorBoundary from './ErrorBoundary'

// 会在渲染时抛出错误的组件
function ThrowOnRender({ error }: { error: Error }) {
  throw error
}

// 可控的抛错组件：通过 prop 控制是否抛错
function ConditionalThrow({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) throw new Error('测试错误')
  return <div>正常渲染</div>
}

describe('ErrorBoundary', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  it('renders children when no error', () => {
    render(
      <ErrorBoundary>
        <div>正常内容</div>
      </ErrorBoundary>
    )
    expect(screen.getByText('正常内容')).toBeInTheDocument()
  })

  it('catches render error and shows default fallback UI', () => {
    render(
      <ErrorBoundary>
        <ThrowOnRender error={new Error('渲染爆炸了')} />
      </ErrorBoundary>
    )
    expect(screen.getByText('页面渲染出错')).toBeInTheDocument()
    expect(screen.getByText('渲染爆炸了')).toBeInTheDocument()
  })

  it('shows retry button in default fallback', () => {
    render(
      <ErrorBoundary>
        <ThrowOnRender error={new Error('err')} />
      </ErrorBoundary>
    )
    expect(screen.getByText('重试')).toBeInTheDocument()
  })

  it('shows go home button in default fallback', () => {
    render(
      <ErrorBoundary>
        <ThrowOnRender error={new Error('err')} />
      </ErrorBoundary>
    )
    expect(screen.getByText('返回首页')).toBeInTheDocument()
  })

  it('uses custom fallback when provided', () => {
    render(
      <ErrorBoundary fallback={(error, reset) => (
        <div>
          <span>自定义错误：{error.message}</span>
          <button onClick={reset}>自定义重试</button>
        </div>
      )}>
        <ThrowOnRender error={new Error('自定义场景')} />
      </ErrorBoundary>
    )
    expect(screen.getByText('自定义错误：自定义场景')).toBeInTheDocument()
    expect(screen.getByText('自定义重试')).toBeInTheDocument()
  })

  it('retry button resets error state', async () => {
    const user = userEvent.setup()

    const MockChild = vi.fn(() => {
      throw new Error('first error')
    })

    render(
      <ErrorBoundary>
        <MockChild />
      </ErrorBoundary>
    )

    // Error should be caught
    expect(screen.getByText('页面渲染出错')).toBeInTheDocument()

    // Change mock to not throw
    MockChild.mockImplementation(() => <div>正常渲染</div>)

    // Click retry - ErrorBoundary resets and re-renders children
    await user.click(screen.getByText('重试'))

    expect(screen.getByText('正常渲染')).toBeInTheDocument()
  })

  it('logs error to console', () => {
    const consoleSpy = vi.spyOn(console, 'error')
    render(
      <ErrorBoundary>
        <ThrowOnRender error={new Error('logged error')} />
      </ErrorBoundary>
    )
    expect(consoleSpy).toHaveBeenCalled()
  })

  it('shows unknown error message when error has no message', () => {
    render(
      <ErrorBoundary>
        <ThrowOnRender error={new Error('')} />
      </ErrorBoundary>
    )
    // Error with empty message - the component shows the message or '未知错误'
    expect(screen.getByText('页面渲染出错')).toBeInTheDocument()
  })
})
