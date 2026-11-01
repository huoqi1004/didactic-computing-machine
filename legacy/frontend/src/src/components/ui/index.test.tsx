/**
 * UI 组件库测试
 *
 * 覆盖 PageHeader / Card / StatusBadge / DataTable / Modal / StatCard / FormField
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  PageHeader,
  Card,
  StatusBadge,
  DataTable,
  Modal,
  StatCard,
  FormField,
  inputClass,
  type Column,
} from './index'

/* ===================== PageHeader ===================== */

describe('PageHeader', () => {
  it('renders title and description', () => {
    render(<PageHeader title="测试标题" description="测试描述" />)
    expect(screen.getByText('测试标题')).toBeInTheDocument()
    expect(screen.getByText('测试描述')).toBeInTheDocument()
  })

  it('renders icon when provided', () => {
    render(<PageHeader title="T" icon={<span data-testid="icon">ICON</span>} />)
    expect(screen.getByTestId('icon')).toBeInTheDocument()
  })

  it('renders actions when provided', () => {
    render(<PageHeader title="T" actions={<button>操作</button>} />)
    expect(screen.getByRole('button', { name: '操作' })).toBeInTheDocument()
  })

  it('does not render description when not provided', () => {
    render(<PageHeader title="T" />)
    expect(screen.queryByText('测试描述')).not.toBeInTheDocument()
  })
})

/* ===================== Card ===================== */

describe('Card', () => {
  it('renders children', () => {
    render(<Card><div data-testid="child">内容</div></Card>)
    expect(screen.getByTestId('child')).toBeInTheDocument()
  })

  it('renders title and action', () => {
    render(
      <Card title="卡片标题" action={<button>操作</button>}>
        <div>内容</div>
      </Card>
    )
    expect(screen.getByText('卡片标题')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '操作' })).toBeInTheDocument()
  })

  it('does not render header when no title and no action', () => {
    const { container } = render(<Card><div>内容</div></Card>)
    expect(container.querySelector('.flex.items-center.justify-between')).toBeFalsy()
  })

  it('applies custom className', () => {
    const { container } = render(<Card className="custom-class"><div>x</div></Card>)
    expect(container.firstChild).toHaveClass('custom-class')
  })
})

/* ===================== StatusBadge ===================== */

describe('StatusBadge', () => {
  it('renders children text', () => {
    render(<StatusBadge>运行中</StatusBadge>)
    expect(screen.getByText('运行中')).toBeInTheDocument()
  })

  it('applies default neutral variant', () => {
    const { container } = render(<StatusBadge>状态</StatusBadge>)
    const badge = container.querySelector('span')
    expect(badge).toHaveClass('bg-slate-500/10')
  })

  it('applies success variant', () => {
    const { container } = render(<StatusBadge variant="success">成功</StatusBadge>)
    const badge = container.querySelector('span')
    expect(badge).toHaveClass('bg-accent/10')
  })

  it('applies danger variant', () => {
    const { container } = render(<StatusBadge variant="danger">危险</StatusBadge>)
    const badge = container.querySelector('span')
    expect(badge).toHaveClass('bg-status-failed/10')
  })

  it('renders dot when dot prop is true', () => {
    const { container } = render(<StatusBadge dot>有圆点</StatusBadge>)
    const dot = container.querySelector('span > span')
    expect(dot).toBeInTheDocument()
    expect(dot).toHaveClass('rounded-full')
  })

  it('does not render dot when dot prop is false', () => {
    const { container } = render(<StatusBadge>无圆点</StatusBadge>)
    const dot = container.querySelector('span > span')
    expect(dot).not.toBeInTheDocument()
  })
})

/* ===================== DataTable ===================== */

interface TestRow extends Record<string, any> {
  id: number
  name: string
  status: string
}

const testColumns: Column<TestRow>[] = [
  { key: 'id', label: 'ID', sortable: true },
  { key: 'name', label: '名称', sortable: true },
  { key: 'status', label: '状态' },
]

const testData: TestRow[] = [
  { id: 1, name: 'Alice', status: '活跃' },
  { id: 2, name: 'Bob', status: '暂停' },
  { id: 3, name: 'Charlie', status: '活跃' },
]

describe('DataTable', () => {
  it('renders column headers', () => {
    render(<DataTable columns={testColumns} data={testData} />)
    expect(screen.getByText('ID')).toBeInTheDocument()
    expect(screen.getByText('名称')).toBeInTheDocument()
    expect(screen.getByText('状态')).toBeInTheDocument()
  })

  it('renders data rows', () => {
    render(<DataTable columns={testColumns} data={testData} />)
    expect(screen.getByText('Alice')).toBeInTheDocument()
    expect(screen.getByText('Bob')).toBeInTheDocument()
    expect(screen.getByText('Charlie')).toBeInTheDocument()
  })

  it('shows empty text when data is empty', () => {
    render(<DataTable columns={testColumns} data={[]} emptyText="没有数据" />)
    expect(screen.getByText('没有数据')).toBeInTheDocument()
  })

  it('shows loading spinner when loading is true', () => {
    render(<DataTable columns={testColumns} data={[]} loading />)
    expect(screen.getByText('加载中...')).toBeInTheDocument()
  })

  it('renders custom cell via render function', () => {
    const cols: Column<TestRow>[] = [
      ...testColumns,
      {
        key: 'action',
        label: '操作',
        render: (row) => <button onClick={() => {}}>编辑{row.id}</button>,
      },
    ]
    render(<DataTable columns={cols} data={testData} />)
    expect(screen.getByText('编辑1')).toBeInTheDocument()
    expect(screen.getByText('编辑2')).toBeInTheDocument()
  })

  it('sorts data when clicking sortable column header', async () => {
    const user = userEvent.setup()
    render(<DataTable columns={testColumns} data={testData} />)
    
    // Click on "名称" to sort ascending
    await user.click(screen.getByText('名称'))
    
    const rows = screen.getAllByRole('row')
    // First row after header should be Alice (ascending)
    expect(within(rows[1]).getByText('Alice')).toBeInTheDocument()
    
    // Click again to sort descending
    await user.click(screen.getByText('名称'))
    
    const rowsDesc = screen.getAllByRole('row')
    expect(within(rowsDesc[1]).getByText('Charlie')).toBeInTheDocument()
  })

  it('filters data when searching', async () => {
    const user = userEvent.setup()
    render(
      <DataTable
        columns={testColumns}
        data={testData}
        searchKeys={['name']}
      />
    )
    
    const searchInput = screen.getByPlaceholderText('搜索...')
    await user.type(searchInput, 'Alice')
    
    expect(screen.getByText('Alice')).toBeInTheDocument()
    expect(screen.queryByText('Bob')).not.toBeInTheDocument()
    expect(screen.queryByText('Charlie')).not.toBeInTheDocument()
  })

  it('calls onRowClick when row is clicked', async () => {
    const user = userEvent.setup()
    const onRowClick = vi.fn()
    render(
      <DataTable
        columns={testColumns}
        data={testData}
        onRowClick={onRowClick}
      />
    )
    
    const rows = screen.getAllByRole('row')
    await user.click(rows[1]) // First data row
    
    expect(onRowClick).toHaveBeenCalledWith(testData[0])
  })

  it('shows pagination when data exceeds page size', () => {
    const largeData: TestRow[] = Array.from({ length: 15 }, (_, i) => ({
      id: i + 1,
      name: `User${i + 1}`,
      status: '活跃',
    }))
    
    render(<DataTable columns={testColumns} data={largeData} pageSize={10} />)
    expect(screen.getByText(/第 1\/2 页/)).toBeInTheDocument()
  })

  it('navigates to next page', async () => {
    const user = userEvent.setup()
    const largeData: TestRow[] = Array.from({ length: 15 }, (_, i) => ({
      id: i + 1,
      name: `User${i + 1}`,
      status: '活跃',
    }))
    
    render(<DataTable columns={testColumns} data={largeData} pageSize={10} />)
    
    // Click next page button
    const buttons = screen.getAllByRole('button')
    const nextBtn = buttons.find(b => b.querySelector('svg.lucide-chevron-right')) || buttons[buttons.length - 1]
    await user.click(nextBtn)
    
    expect(screen.getByText('User11')).toBeInTheDocument()
  })

  it('shows record count', () => {
    render(<DataTable columns={testColumns} data={testData} />)
    expect(screen.getByText('3 条记录')).toBeInTheDocument()
  })
})

/* ===================== Modal ===================== */

describe('Modal', () => {
  it('does not render when open is false', () => {
    render(<Modal open={false} onClose={() => {}}><div>内容</div></Modal>)
    expect(screen.queryByText('内容')).not.toBeInTheDocument()
  })

  it('renders children when open', () => {
    render(<Modal open={true} onClose={() => {}}><div>模态框内容</div></Modal>)
    expect(screen.getByText('模态框内容')).toBeInTheDocument()
  })

  it('renders title when provided', () => {
    render(<Modal open={true} onClose={() => {}} title="对话框标题"><div>x</div></Modal>)
    expect(screen.getByText('对话框标题')).toBeInTheDocument()
  })

  it('calls onClose when clicking overlay', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    const { container } = render(<Modal open={true} onClose={onClose}><div>x</div></Modal>)
    
    // Click the overlay (first child div)
    const overlay = container.querySelector('.absolute.inset-0')
    if (overlay) {
      await user.click(overlay)
    }
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose when clicking close button', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<Modal open={true} onClose={onClose} title="T"><div>x</div></Modal>)
    
    const closeBtn = screen.getByRole('button')
    await user.click(closeBtn)
    
    expect(onClose).toHaveBeenCalled()
  })

  it('applies size class', () => {
    const { container } = render(
      <Modal open={true} onClose={() => {}} size="xl"><div>x</div></Modal>
    )
    const modalContent = container.querySelector('.max-w-4xl')
    expect(modalContent).toBeInTheDocument()
  })
})

/* ===================== StatCard ===================== */

describe('StatCard', () => {
  it('renders label and value', () => {
    render(<StatCard label="总收入" value="¥100,000" />)
    expect(screen.getByText('总收入')).toBeInTheDocument()
    expect(screen.getByText('¥100,000')).toBeInTheDocument()
  })

  it('renders icon when provided', () => {
    render(<StatCard label="L" value="V" icon={<span data-testid="icon">📊</span>} />)
    expect(screen.getByTestId('icon')).toBeInTheDocument()
  })

  it('renders trend with up arrow', () => {
    render(<StatCard label="L" value="V" trend="10%" trendUp={true} />)
    expect(screen.getByText(/10%/)).toBeInTheDocument()
    expect(screen.getByText(/↑/)).toBeInTheDocument()
  })

  it('renders trend with down arrow', () => {
    render(<StatCard label="L" value="V" trend="5%" trendUp={false} />)
    expect(screen.getByText(/↓/)).toBeInTheDocument()
  })

  it('does not render trend when not provided', () => {
    render(<StatCard label="L" value="V" />)
    expect(screen.queryByText(/↑|↓/)).not.toBeInTheDocument()
  })
})

/* ===================== FormField ===================== */

describe('FormField', () => {
  it('renders label', () => {
    render(<FormField label="用户名"><input /></FormField>)
    expect(screen.getByText('用户名')).toBeInTheDocument()
  })

  it('renders required indicator', () => {
    render(<FormField label="用户名" required><input /></FormField>)
    expect(screen.getByText('*')).toBeInTheDocument()
  })

  it('does not render required indicator when not required', () => {
    render(<FormField label="用户名"><input /></FormField>)
    expect(screen.queryByText('*')).not.toBeInTheDocument()
  })

  it('renders hint when provided', () => {
    render(<FormField label="L" hint="请输入3-20个字符"><input /></FormField>)
    expect(screen.getByText('请输入3-20个字符')).toBeInTheDocument()
  })

  it('renders children', () => {
    render(<FormField label="L"><input data-testid="input" /></FormField>)
    expect(screen.getByTestId('input')).toBeInTheDocument()
  })
})

/* ===================== inputClass ===================== */

describe('inputClass', () => {
  it('is a non-empty string', () => {
    expect(typeof inputClass).toBe('string')
    expect(inputClass.length).toBeGreaterThan(0)
  })

  it('contains focus styling', () => {
    expect(inputClass).toContain('focus:outline-none')
  })
})
