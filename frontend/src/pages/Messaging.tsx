import { useState, useEffect, useRef, useCallback } from 'react'
import {
  MessageSquare,
  Users,
  Send,
  Paperclip,
  Bot,
  Download,
  Plus,
  Hash,
  User,
  Search,
  X,
  PanelRightClose,
  PanelRightOpen,
  File as FileIcon,
  Loader2,
  Circle,
  Menu,
  ArrowLeft,
} from 'lucide-react'
import { useChatStore, type ChatChannel, type ChatMessage } from '../stores/chatStore'
import { useAuthStore } from '../stores/authStore'
import { apiService } from '../services/api'
import { Modal, FormField, inputClass } from '../components/ui/index'
import { cn } from '../lib/utils'

/* ===================== 工具函数 ===================== */

function formatTime(iso: string | null): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const now = new Date()
    const isToday = d.toDateString() === now.toDateString()
    if (isToday) {
      return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    }
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  } catch {
    return ''
  }
}

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`
}

function getChannelDisplayName(channel: ChatChannel, userId: string): string {
  if (channel.name) return channel.name
  if (channel.channel_type === 'direct') {
    const other = channel.members?.find((m) => m.user_id !== userId)
    return other?.user_id ? `与 ${other.user_id} 的对话` : '单聊'
  }
  if (channel.channel_type === 'agent') return 'Agent 对话'
  return '群聊'
}

function getChannelIcon(channel: ChatChannel) {
  if (channel.channel_type === 'agent') return Bot
  if (channel.channel_type === 'direct') return User
  return Hash
}

/* ===================== 内部错误边界 ===================== */

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

function useErrorBoundary() {
  const [state, setState] = useState<ErrorBoundaryState>({ hasError: false, error: null })
  const reset = useCallback(() => setState({ hasError: false, error: null }), [])
  const capture = useCallback((error: Error) => setState({ hasError: true, error }), [])
  return { ...state, reset, capture }
}

/* ===================== 频道列表项 ===================== */

function ChannelItem({
  channel,
  active,
  userId,
  onClick,
}: {
  channel: ChatChannel
  active: boolean
  userId: string
  onClick: () => void
}) {
  const Icon = getChannelIcon(channel)
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 text-left',
        active
          ? 'bg-primary/15 border border-primary/30'
          : 'border border-transparent hover:bg-bg-hover',
      )}
    >
      <div
        className={cn(
          'w-9 h-9 rounded-lg flex items-center justify-center shrink-0',
          active ? 'bg-primary/20 text-primary' : 'bg-bg-input text-slate-400',
        )}
      >
        <Icon className="w-[18px] h-[18px]" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <span
            className={cn(
              'text-sm font-medium truncate',
              active ? 'text-primary' : 'text-slate-300',
            )}
          >
            {getChannelDisplayName(channel, userId)}
          </span>
          {channel.last_message_at && (
            <span className="text-[10px] text-slate-600 shrink-0">
              {formatTime(channel.last_message_at)}
            </span>
          )}
        </div>
        <div className="flex items-center justify-between gap-2 mt-0.5">
          <span className="text-xs text-slate-500 truncate">
            {channel.last_message_preview || '暂无消息'}
          </span>
          {channel.unread_count > 0 && (
            <span className="shrink-0 min-w-[18px] h-[18px] px-1 rounded-full bg-primary text-white text-[10px] font-bold flex items-center justify-center">
              {channel.unread_count > 99 ? '99+' : channel.unread_count}
            </span>
          )}
        </div>
      </div>
    </button>
  )
}

/* ===================== 消息气泡 ===================== */

function MessageBubble({ message, isSelf, onDownload }: { message: ChatMessage; isSelf: boolean; onDownload: (fileId: string, filename: string) => void }) {
  // 系统消息居中显示
  if (message.sender_type === 'system' || message.message_type === 'system') {
    return (
      <div className="flex justify-center my-2">
        <span className="text-xs text-slate-600 bg-bg-input px-3 py-1 rounded-full">
          {message.content}
        </span>
      </div>
    )
  }

  const isAgent = message.sender_type === 'agent'

  return (
    <div className={cn('flex gap-2.5 mb-3', isSelf ? 'flex-row-reverse' : 'flex-row')}>
      {/* 头像 */}
      <div
        className={cn(
          'w-8 h-8 rounded-lg flex items-center justify-center shrink-0 text-xs font-bold',
          isSelf
            ? 'bg-primary/20 text-primary'
            : isAgent
              ? 'bg-accent/20 text-accent'
              : 'bg-bg-input text-slate-400',
        )}
      >
        {isAgent ? <Bot className="w-4 h-4" /> : message.sender_name?.charAt(0)?.toUpperCase() || 'U'}
      </div>

      {/* 消息内容 */}
      <div className={cn('max-w-[70%] min-w-0', isSelf && 'items-end')}>
        <div className={cn('flex items-center gap-2 mb-1', isSelf && 'flex-row-reverse')}>
          <span className="text-xs font-medium text-slate-300">{isSelf ? '我' : message.sender_name}</span>
          {isAgent && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/15 text-accent border border-accent/20">
              Agent
            </span>
          )}
          <span className="text-[10px] text-slate-600">{formatTime(message.created_at)}</span>
        </div>

        {message.message_type === 'file' && message.file_info ? (
          <div
            className={cn(
              'flex items-center gap-3 px-3 py-2.5 rounded-lg border min-w-0',
              isSelf
                ? 'bg-primary/15 border-primary/30'
                : 'bg-bg-input border-[#2a3142]',
            )}
          >
            <div className="w-9 h-9 rounded-lg bg-bg-card flex items-center justify-center shrink-0">
              <FileIcon className="w-4 h-4 text-slate-400" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm text-white truncate">{message.file_info.filename}</div>
              <div className="text-[10px] text-slate-500">{formatFileSize(message.file_info.file_size)}</div>
            </div>
            {message.file_info.file_id && (
              <button
                onClick={() => onDownload(message.file_info!.file_id, message.file_info!.filename)}
                className="shrink-0 p-2 rounded-lg hover:bg-bg-hover text-slate-400 hover:text-primary transition-colors"
                title="下载文件"
              >
                <Download className="w-4 h-4" />
              </button>
            )}
          </div>
        ) : (
          <div
            className={cn(
              'px-3.5 py-2 rounded-lg text-sm break-words',
              isSelf
                ? 'bg-primary/20 text-white rounded-tr-sm'
                : isAgent
                  ? 'bg-accent/10 text-slate-200 border border-accent/20 rounded-tl-sm'
                  : 'bg-bg-input text-slate-200 rounded-tl-sm',
            )}
          >
            {message.content}
          </div>
        )}
      </div>
    </div>
  )
}

/* ===================== 新建频道 Modal ===================== */

function CreateChannelModal({
  open,
  onClose,
  userId,
  onCreated,
}: {
  open: boolean
  onClose: () => void
  userId: string
  onCreated: (channelId: string) => void
}) {
  const [channelType, setChannelType] = useState<'group' | 'direct'>('group')
  const [channelName, setChannelName] = useState('')
  const [memberIds, setMemberIds] = useState<string[]>([])
  const [users, setUsers] = useState<any[]>([])
  const [agents, setAgents] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const createChannel = useChatStore((s) => s.createChannel)

  useEffect(() => {
    if (!open) return
    setChannelType('group')
    setChannelName('')
    setMemberIds([])
    // 拉取用户和 Agent 列表
    Promise.all([apiService.getChatUsers(), apiService.getMessagingAgents()]).then(([u, a]) => {
      setUsers(Array.isArray(u) ? u : [])
      setAgents(Array.isArray(a) ? a : [])
    })
  }, [open])

  const toggleMember = (id: string) => {
    if (channelType === 'direct') {
      setMemberIds([id])
    } else {
      setMemberIds((prev) => (prev.includes(id) ? prev.filter((m) => m !== id) : [...prev, id]))
    }
  }

  const handleSubmit = async () => {
    if (memberIds.length === 0) return
    if (channelType === 'group' && !channelName.trim()) return
    setLoading(true)
    try {
      const name = channelType === 'group' ? channelName.trim() : ''
      const channelId = await createChannel(channelType, name, [...memberIds, userId])
      if (channelId) {
        onCreated(channelId)
      }
      onClose()
    } catch {
      // 降级处理
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="新建频道" size="md">
      <div className="space-y-4">
        {/* 频道类型 */}
        <FormField label="频道类型" required>
          <div className="grid grid-cols-2 gap-3">
            <button
              onClick={() => {
                setChannelType('group')
                setMemberIds([])
              }}
              className={cn(
                'flex items-center gap-2 px-4 py-3 rounded-lg border transition-colors',
                channelType === 'group'
                  ? 'bg-primary/15 border-primary/40 text-primary'
                  : 'bg-bg-input border-[#2a3142] text-slate-400 hover:border-slate-600',
              )}
            >
              <Hash className="w-4 h-4" />
              <span className="text-sm font-medium">群聊</span>
            </button>
            <button
              onClick={() => {
                setChannelType('direct')
                setMemberIds([])
              }}
              className={cn(
                'flex items-center gap-2 px-4 py-3 rounded-lg border transition-colors',
                channelType === 'direct'
                  ? 'bg-primary/15 border-primary/40 text-primary'
                  : 'bg-bg-input border-[#2a3142] text-slate-400 hover:border-slate-600',
              )}
            >
              <User className="w-4 h-4" />
              <span className="text-sm font-medium">单聊</span>
            </button>
          </div>
        </FormField>

        {/* 群聊名称 */}
        {channelType === 'group' && (
          <FormField label="群聊名称" required>
            <input
              type="text"
              value={channelName}
              onChange={(e) => setChannelName(e.target.value)}
              placeholder="请输入群聊名称"
              className={inputClass}
              maxLength={50}
            />
          </FormField>
        )}

        {/* 选择成员 */}
        <FormField
          label={channelType === 'direct' ? '选择成员（单选）' : '选择成员（多选）'}
          required
          hint={channelType === 'direct' ? '请选择一位对话对象' : `已选择 ${memberIds.length} 人`}
        >
          <div className="max-h-64 overflow-y-auto space-y-1 rounded-lg border border-[#2a3142] p-2">
            {users.length === 0 && agents.length === 0 && (
              <div className="text-center py-6 text-sm text-slate-600">暂无可选成员</div>
            )}
            {/* 用户列表 */}
            {users.map((u) => {
              const id = u.user_id ?? u.id ?? ''
              const name = u.username ?? u.name ?? id
              const selected = memberIds.includes(id)
              return (
                <button
                  key={`user-${id}`}
                  onClick={() => toggleMember(id)}
                  className={cn(
                    'w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-left',
                    selected ? 'bg-primary/15' : 'hover:bg-bg-hover',
                  )}
                >
                  <div className="w-8 h-8 rounded-full bg-bg-input flex items-center justify-center shrink-0">
                    <User className="w-4 h-4 text-slate-400" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-slate-200 truncate">{name}</div>
                    {u.department && <div className="text-[10px] text-slate-500">{u.department}</div>}
                  </div>
                  {selected && <Circle className="w-4 h-4 text-primary fill-primary" />}
                </button>
              )
            })}
            {/* Agent 列表 */}
            {agents.map((a) => {
              const id = a.agent_id ?? a.id ?? ''
              const name = a.name ?? a.agent_name ?? id
              const selected = memberIds.includes(id)
              return (
                <button
                  key={`agent-${id}`}
                  onClick={() => toggleMember(id)}
                  className={cn(
                    'w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-colors text-left',
                    selected ? 'bg-primary/15' : 'hover:bg-bg-hover',
                  )}
                >
                  <div className="w-8 h-8 rounded-full bg-accent/15 flex items-center justify-center shrink-0">
                    <Bot className="w-4 h-4 text-accent" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-slate-200 truncate">{name}</div>
                    {a.type && <div className="text-[10px] text-slate-500">{a.type}</div>}
                  </div>
                  {selected && <Circle className="w-4 h-4 text-primary fill-primary" />}
                </button>
              )
            })}
          </div>
        </FormField>

        {/* 操作按钮 */}
        <div className="flex justify-end gap-3 pt-2">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-sm text-slate-400 hover:text-white transition-colors"
          >
            取消
          </button>
          <button
            onClick={handleSubmit}
            disabled={loading || memberIds.length === 0 || (channelType === 'group' && !channelName.trim())}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-primary text-white hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
          >
            {loading && <Loader2 className="w-4 h-4 animate-spin" />}
            创建
          </button>
        </div>
      </div>
    </Modal>
  )
}

/* ===================== 主组件 ===================== */

export default function Messaging() {
  const {
    channels,
    activeChannelId,
    messages,
    onlineUsers,
    loading,
    sending,
    wsConnected,
    initChat,
    setActiveChannel,
    sendMessage,
    uploadFile,
    disconnect,
  } = useChatStore()

  const { user, token } = useAuthStore()
  const userId = user?.user_id ?? 'me'

  const [inputText, setInputText] = useState('')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [showMembers, setShowMembers] = useState(true)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [members, setMembers] = useState<any[]>([])
  const [error, setError] = useState<string | null>(null)
  const errorBoundary = useErrorBoundary()

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // 初始化聊天
  useEffect(() => {
    if (token && userId) {
      try {
        initChat(token, userId)
      } catch (err) {
        errorBoundary.capture(err as Error)
      }
    }
    return () => {
      disconnect()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, userId])

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // 拉取当前频道成员
  useEffect(() => {
    if (!activeChannelId) {
      setMembers([])
      return
    }
    apiService
      .getChannelMembers(activeChannelId)
      .then((m) => setMembers(Array.isArray(m) ? m : []))
      .catch(() => setMembers([]))
  }, [activeChannelId])

  const activeChannel = channels.find((c) => c.channel_id === activeChannelId)
  const isAgentChannel = activeChannel?.channel_type === 'agent'

  const filteredChannels = searchQuery
    ? channels.filter((c) =>
        getChannelDisplayName(c, userId).toLowerCase().includes(searchQuery.toLowerCase()),
      )
    : channels

  const handleSend = useCallback(() => {
    if (!inputText.trim() || !activeChannelId || sending) return
    const content = inputText.trim()
    setInputText('')
    // 重置 textarea 高度
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
    sendMessage(activeChannelId, content).catch((err) => {
      setError('消息发送失败，请重试')
      setTimeout(() => setError(null), 3000)
    })
  }, [inputText, activeChannelId, sending, sendMessage])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file && activeChannelId) {
      uploadFile(activeChannelId, file)
    }
    // 重置 input 以便重复选择同一文件
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const handleDownload = async (fileId: string, filename: string) => {
    try {
      const blob = await apiService.downloadChatFile(fileId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch {
      setError('文件下载失败')
      setTimeout(() => setError(null), 3000)
    }
  }

  const handleSelectChannel = (channelId: string) => {
    setActiveChannel(channelId)
    setSidebarOpen(false)
  }

  // 错误边界渲染
  if (errorBoundary.hasError) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-8rem)]">
        <div className="text-center">
          <div className="w-16 h-16 rounded-full bg-status-failed/10 flex items-center justify-center mx-auto mb-4">
            <X className="w-8 h-8 text-status-failed" />
          </div>
          <h2 className="text-lg font-semibold text-white mb-2">聊天模块出错</h2>
          <p className="text-sm text-slate-500 mb-4">{errorBoundary.error?.message || '未知错误'}</p>
          <button
            onClick={errorBoundary.reset}
            className="px-4 py-2 rounded-lg bg-primary text-white text-sm hover:bg-primary/90 transition-colors"
          >
            重试
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-0 animate-fade-in">
      {/* ========== 左侧：频道列表 ========== */}
      <aside
        className={cn(
          'w-72 shrink-0 bg-bg-card/60 border-r border-[#1e2536] flex flex-col',
          'fixed lg:relative inset-y-0 left-0 z-40 lg:z-auto transition-transform duration-300',
          sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0',
        )}
      >
        {/* 头部 */}
        <div className="h-14 flex items-center justify-between px-4 border-b border-[#1e2536] shrink-0">
          <div className="flex items-center gap-2">
            <MessageSquare className="w-4 h-4 text-primary" />
            <span className="text-sm font-semibold text-white">内部通讯</span>
          </div>
          <button
            onClick={() => setShowCreateModal(true)}
            className="p-1.5 rounded-lg bg-primary/15 text-primary hover:bg-primary/25 transition-colors"
            title="新建频道"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>

        {/* 搜索 */}
        <div className="p-3 border-b border-[#1e2536] shrink-0">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-600" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索频道..."
              className="w-full pl-9 pr-3 py-2 rounded-lg bg-bg-input border border-[#2a3142] text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-primary/40 transition-colors"
            />
          </div>
        </div>

        {/* 频道列表 */}
        <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
          {loading && channels.length === 0 ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-5 h-5 text-primary animate-spin" />
            </div>
          ) : filteredChannels.length === 0 ? (
            <div className="text-center py-8">
              <MessageSquare className="w-8 h-8 text-slate-700 mx-auto mb-2" />
              <p className="text-xs text-slate-600">
                {searchQuery ? '未找到匹配频道' : '暂无频道，点击 + 创建'}
              </p>
            </div>
          ) : (
            filteredChannels.map((channel) => (
              <ChannelItem
                key={channel.channel_id}
                channel={channel}
                active={channel.channel_id === activeChannelId}
                userId={userId}
                onClick={() => handleSelectChannel(channel.channel_id)}
              />
            ))
          )}
        </div>

        {/* WebSocket 状态 */}
        <div className="p-3 border-t border-[#1e2536] shrink-0">
          <div className="flex items-center gap-2 px-2">
            <span
              className={cn(
                'w-2 h-2 rounded-full',
                wsConnected ? 'bg-accent' : 'bg-status-paused',
              )}
            />
            <span className="text-[11px] text-slate-500">
              {wsConnected ? '实时连接' : '离线模式'}
            </span>
          </div>
        </div>
      </aside>

      {/* 移动端遮罩 */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/60 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ========== 中间：消息区域 ========== */}
      <div className="flex-1 flex flex-col min-w-0 bg-bg-card/40">
        {!activeChannel ? (
          /* 空状态 */
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <button
                onClick={() => setSidebarOpen(true)}
                className="lg:hidden mb-4 mx-auto flex items-center gap-2 px-4 py-2 rounded-lg bg-bg-input border border-[#2a3142] text-slate-400 text-sm"
              >
                <Menu className="w-4 h-4" />
                打开频道列表
              </button>
              <MessageSquare className="w-12 h-12 text-slate-700 mx-auto mb-3" />
              <h3 className="text-base font-medium text-slate-400 mb-1">选择一个频道开始对话</h3>
              <p className="text-sm text-slate-600">或点击左上角 + 创建新频道</p>
            </div>
          </div>
        ) : (
          <>
            {/* 顶部：频道信息 */}
            <div className="h-14 flex items-center justify-between px-4 border-b border-[#1e2536] shrink-0">
              <div className="flex items-center gap-3 min-w-0">
                <button
                  onClick={() => setSidebarOpen(true)}
                  className="lg:hidden text-slate-400 hover:text-white shrink-0"
                >
                  <ArrowLeft className="w-5 h-5" />
                </button>
                {(() => {
                  const Icon = getChannelIcon(activeChannel)
                  return (
                    <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                      <Icon className="w-4 h-4 text-primary" />
                    </div>
                  )
                })()}
                <div className="min-w-0">
                  <h2 className="text-sm font-semibold text-white truncate">
                    {getChannelDisplayName(activeChannel, userId)}
                  </h2>
                  <p className="text-[11px] text-slate-500">
                    {activeChannel.channel_type === 'group' && `${activeChannel.members?.length ?? 0} 名成员`}
                    {activeChannel.channel_type === 'direct' && '单聊'}
                    {activeChannel.channel_type === 'agent' && 'Agent 对话'}
                  </p>
                </div>
              </div>
              <button
                onClick={() => setShowMembers(!showMembers)}
                className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-bg-hover transition-colors shrink-0"
                title={showMembers ? '隐藏成员' : '显示成员'}
              >
                {showMembers ? <PanelRightClose className="w-5 h-5" /> : <PanelRightOpen className="w-5 h-5" />}
              </button>
            </div>

            {/* 消息列表 */}
            <div className="flex-1 overflow-y-auto px-4 py-4">
              {loading && messages.length === 0 ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="w-6 h-6 text-primary animate-spin" />
                </div>
              ) : messages.length === 0 ? (
                <div className="flex items-center justify-center py-12">
                  <p className="text-sm text-slate-600">暂无消息，发送第一条消息开始对话</p>
                </div>
              ) : (
                messages.map((msg) => (
                  <MessageBubble
                    key={msg.message_id}
                    message={msg}
                    isSelf={msg.sender_id === userId}
                    onDownload={handleDownload}
                  />
                ))
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* 错误提示 */}
            {error && (
              <div className="px-4 py-2 bg-status-failed/10 border-t border-status-failed/20">
                <p className="text-xs text-status-failed">{error}</p>
              </div>
            )}

            {/* 底部：输入栏 */}
            <div className="shrink-0 border-t border-[#1e2536] p-3">
              {/* Agent 指令模式提示 */}
              {isAgentChannel && (
                <div className="flex items-center gap-2 mb-2 px-3 py-1.5 rounded-lg bg-accent/10 border border-accent/20">
                  <Bot className="w-3.5 h-3.5 text-accent shrink-0" />
                  <span className="text-[11px] text-accent">指令模式：输入内容将作为指令发送给 Agent</span>
                </div>
              )}

              <div className="flex items-end gap-2">
                {/* 文件上传 */}
                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  onChange={handleFileSelect}
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={sending}
                  className="shrink-0 p-2.5 rounded-lg bg-bg-input border border-[#2a3142] text-slate-400 hover:text-white hover:border-primary/30 transition-colors disabled:opacity-40"
                  title="上传文件"
                >
                  <Paperclip className="w-4 h-4" />
                </button>

                {/* 文本输入 */}
                <textarea
                  ref={textareaRef}
                  value={inputText}
                  onChange={(e) => {
                    setInputText(e.target.value)
                    // 自适应高度
                    e.target.style.height = 'auto'
                    e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`
                  }}
                  onKeyDown={handleKeyDown}
                  placeholder={isAgentChannel ? '输入指令...' : '输入消息，回车发送，Shift+回车换行'}
                  rows={1}
                  disabled={sending}
                  className="flex-1 resize-none px-3 py-2.5 rounded-lg bg-bg-input border border-[#2a3142] text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-primary/40 transition-colors disabled:opacity-40 max-h-[120px]"
                />

                {/* 发送按钮 */}
                <button
                  onClick={handleSend}
                  disabled={!inputText.trim() || sending}
                  className="shrink-0 p-2.5 rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center justify-center"
                  title="发送"
                >
                  {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      {/* ========== 右侧：成员列表（可折叠） ========== */}
      {showMembers && activeChannel && (
        <aside className="w-60 shrink-0 bg-bg-card/60 border-l border-[#1e2536] flex flex-col hidden md:flex">
          {/* 头部 */}
          <div className="h-14 flex items-center justify-between px-4 border-b border-[#1e2536] shrink-0">
            <div className="flex items-center gap-2">
              <Users className="w-4 h-4 text-slate-400" />
              <span className="text-sm font-semibold text-white">成员</span>
            </div>
            <span className="text-xs text-slate-500">{members.length}</span>
          </div>

          {/* 成员列表 */}
          <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
            {members.length === 0 ? (
              <div className="text-center py-8">
                <Users className="w-8 h-8 text-slate-700 mx-auto mb-2" />
                <p className="text-xs text-slate-600">暂无成员信息</p>
              </div>
            ) : (
              members.map((m) => {
                const id = m.user_id ?? m.id ?? ''
                const name = m.username ?? m.name ?? m.user_id ?? id
                const isAgent = m.member_type === 'agent'
                const isOnline = onlineUsers.includes(id)
                return (
                  <div
                    key={id}
                    className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-bg-hover transition-colors"
                  >
                    <div className="relative shrink-0">
                      <div
                        className={cn(
                          'w-8 h-8 rounded-full flex items-center justify-center',
                          isAgent ? 'bg-accent/15' : 'bg-bg-input',
                        )}
                      >
                        {isAgent ? (
                          <Bot className="w-4 h-4 text-accent" />
                        ) : (
                          <span className="text-xs font-bold text-slate-400">
                            {name?.charAt(0)?.toUpperCase() || 'U'}
                          </span>
                        )}
                      </div>
                      {/* 在线状态 */}
                      <span
                        className={cn(
                          'absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border-2 border-bg-card',
                          isOnline ? 'bg-accent' : 'bg-slate-600',
                        )}
                      />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-slate-200 truncate">{name}</div>
                      <div className="text-[10px] text-slate-500">
                        {isAgent ? 'Agent' : isOnline ? '在线' : '离线'}
                      </div>
                    </div>
                    {m.role === 'owner' && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/15 text-primary">
                        群主
                      </span>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </aside>
      )}

      {/* 新建频道 Modal */}
      <CreateChannelModal
        open={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        userId={userId}
        onCreated={(channelId) => {
          handleSelectChannel(channelId)
        }}
      />
    </div>
  )
}
