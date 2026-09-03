import { create } from 'zustand'
import { apiService } from '../services/api'

/* ===================== 类型定义 ===================== */

export interface ChatChannel {
  channel_id: string
  channel_type: 'group' | 'direct' | 'agent'
  name: string | null
  avatar: string | null
  created_by: string
  last_message_at: string | null
  last_message_preview: string | null
  unread_count: number
  members?: ChatMember[]
}

export interface ChatMember {
  user_id: string
  member_type: 'user' | 'agent'
  role: 'owner' | 'admin' | 'member'
  joined_at: string
  last_read_message_id: number | null
}

export interface ChatMessage {
  message_id: number
  channel_id: string
  sender_id: string
  sender_type: 'user' | 'agent' | 'system'
  sender_name: string
  content: string
  message_type: 'text' | 'file' | 'command' | 'system'
  file_info?: { file_id: string; filename: string; file_size: number; mime_type: string }
  reply_to_id?: number | null
  created_at: string
}

interface ChatState {
  channels: ChatChannel[]
  activeChannelId: string | null
  messages: ChatMessage[]
  onlineUsers: string[]
  loading: boolean
  sending: boolean
  wsConnected: boolean
  // Actions
  initChat: (token: string, userId: string) => void
  fetchChannels: () => Promise<void>
  fetchMessages: (channelId: string) => Promise<void>
  sendMessage: (channelId: string, content: string, messageType?: string) => Promise<void>
  createChannel: (type: 'group' | 'direct', name: string, memberIds: string[]) => Promise<string | null>
  markAsRead: (channelId: string) => Promise<void>
  uploadFile: (channelId: string, file: File) => Promise<void>
  setActiveChannel: (channelId: string) => void
  receiveMessage: (message: ChatMessage) => void
  disconnect: () => void
}

/* ===================== WebSocket 管理 ===================== */

const WS_BASE_URL = 'ws://localhost:8000/api/v1/ws/chat'
const HEARTBEAT_INTERVAL = 30000
const MAX_RECONNECT_ATTEMPTS = 6

let wsInstance: WebSocket | null = null
let heartbeatTimer: ReturnType<typeof setInterval> | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempts = 0
let currentToken: string | null = null
let currentUserId: string | null = null

function clearHeartbeat(): void {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

function startHeartbeat(): void {
  clearHeartbeat()
  heartbeatTimer = setInterval(() => {
    if (wsInstance && wsInstance.readyState === WebSocket.OPEN) {
      try {
        wsInstance.send(JSON.stringify({ type: 'ping' }))
      } catch {
        // 忽略发送失败
      }
    }
  }, HEARTBEAT_INTERVAL)
}

function scheduleReconnect(): void {
  if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) return
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectAttempts++
  const delay = Math.min(1000 * 2 ** reconnectAttempts, 30000)
  reconnectTimer = setTimeout(() => {
    if (currentToken) {
      connectWebSocket(currentToken)
    }
  }, delay)
}

function connectWebSocket(token: string): void {
  // 清理已有连接
  if (wsInstance) {
    wsInstance.onopen = null
    wsInstance.onmessage = null
    wsInstance.onerror = null
    wsInstance.onclose = null
    if (wsInstance.readyState === WebSocket.OPEN || wsInstance.readyState === WebSocket.CONNECTING) {
      wsInstance.close()
    }
    wsInstance = null
  }
  clearHeartbeat()

  try {
    wsInstance = new WebSocket(`${WS_BASE_URL}?token=${token}`)
  } catch {
    useChatStore.getState().disconnect()
    return
  }

  wsInstance.onopen = () => {
    reconnectAttempts = 0
    useChatStore.setState({ wsConnected: true })
    startHeartbeat()
  }

  wsInstance.onmessage = (event: MessageEvent) => {
    try {
      const data = JSON.parse(event.data)
      const type = data.type || data.event_type

      if (type === 'chat_message' || type === 'message') {
        const message: ChatMessage = {
          message_id: data.message_id ?? data.id ?? Date.now(),
          channel_id: data.channel_id,
          sender_id: data.sender_id ?? data.sender ?? '',
          sender_type: data.sender_type ?? 'user',
          sender_name: data.sender_name ?? data.sender_name ?? '未知',
          content: data.content ?? '',
          message_type: data.message_type ?? 'text',
          file_info: data.file_info,
          reply_to_id: data.reply_to_id ?? null,
          created_at: data.created_at ?? data.timestamp ?? new Date().toISOString(),
        }
        useChatStore.getState().receiveMessage(message)
      } else if (type === 'new_channel' || type === 'channel_created') {
        useChatStore.getState().fetchChannels()
      } else if (type === 'presence' || type === 'online_users') {
        const users = data.users ?? data.online_users ?? []
        if (Array.isArray(users)) {
          useChatStore.setState({ onlineUsers: users })
        }
      } else if (type === 'pong' || type === 'ping') {
        // 心跳响应，忽略
      }
    } catch {
      // 忽略无法解析的消息
    }
  }

  wsInstance.onerror = () => {
    // 错误后触发关闭逻辑
    wsInstance?.close()
  }

  wsInstance.onclose = () => {
    useChatStore.setState({ wsConnected: false })
    clearHeartbeat()
    // 尝试重连
    if (currentToken) {
      scheduleReconnect()
    }
  }
}

/* ===================== Store 定义 ===================== */

export const useChatStore = create<ChatState>((set, get) => ({
  channels: [],
  activeChannelId: null,
  messages: [],
  onlineUsers: [],
  loading: false,
  sending: false,
  wsConnected: false,

  initChat: (token, userId) => {
    currentToken = token
    currentUserId = userId
    // 建立 WebSocket 连接
    connectWebSocket(token)
    // 拉取频道列表
    get().fetchChannels()
  },

  fetchChannels: async () => {
    set({ loading: true })
    try {
      const rawChannels = await apiService.getChatChannels()
      const channels: ChatChannel[] = (Array.isArray(rawChannels) ? rawChannels : []).map((c: any) => ({
        channel_id: c.channel_id ?? c.id ?? '',
        channel_type: c.channel_type ?? c.type ?? 'group',
        name: c.name ?? null,
        avatar: c.avatar ?? null,
        created_by: c.created_by ?? '',
        last_message_at: c.last_message_at ?? c.last_message_time ?? null,
        last_message_preview: c.last_message_preview ?? c.last_message ?? null,
        unread_count: c.unread_count ?? 0,
        members: c.members,
      }))
      set({ channels, loading: false })
    } catch {
      set({ channels: [], loading: false })
    }
  },

  fetchMessages: async (channelId) => {
    set({ loading: true })
    try {
      const rawMessages = await apiService.getChannelMessages(channelId)
      const messages: ChatMessage[] = (Array.isArray(rawMessages) ? rawMessages : []).map((m: any) => ({
        message_id: m.message_id ?? m.id ?? Date.now(),
        channel_id: m.channel_id ?? channelId,
        sender_id: m.sender_id ?? m.sender ?? '',
        sender_type: m.sender_type ?? 'user',
        sender_name: m.sender_name ?? m.sender_name ?? '未知',
        content: m.content ?? '',
        message_type: m.message_type ?? 'text',
        file_info: m.file_info,
        reply_to_id: m.reply_to_id ?? null,
        created_at: m.created_at ?? m.timestamp ?? new Date().toISOString(),
      }))
      // 按时间正序排列
      messages.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
      set({ messages, loading: false })
    } catch {
      set({ messages: [], loading: false })
    }
  },

  sendMessage: async (channelId, content, messageType = 'text') => {
    if (!content.trim()) return
    const userId = currentUserId || 'me'
    set({ sending: true })

    // 乐观更新：先在本地追加消息
    const optimisticMessage: ChatMessage = {
      message_id: Date.now(),
      channel_id: channelId,
      sender_id: userId,
      sender_type: 'user',
      sender_name: '我',
      content,
      message_type: messageType as ChatMessage['message_type'],
      created_at: new Date().toISOString(),
    }
    set((state) => ({ messages: [...state.messages, optimisticMessage] }))

    try {
      await apiService.sendChannelMessage(channelId, content, messageType)
      // 更新频道列表中的最后消息预览
      set((state) => ({
        channels: state.channels.map((ch) =>
          ch.channel_id === channelId
            ? {
                ...ch,
                last_message_preview: content,
                last_message_at: new Date().toISOString(),
              }
            : ch,
        ),
      }))
    } catch {
      // 发送失败时移除乐观消息
      set((state) => ({
        messages: state.messages.filter((m) => m.message_id !== optimisticMessage.message_id),
      }))
    } finally {
      set({ sending: false })
    }
  },

  createChannel: async (type, name, memberIds) => {
    try {
      const res = await apiService.createChatChannel(type, name, memberIds)
      const channelId = res?.channel_id ?? res?.id ?? null
      if (channelId) {
        await get().fetchChannels()
      }
      return channelId
    } catch {
      return null
    }
  },

  markAsRead: async (channelId) => {
    try {
      await apiService.markChannelRead(channelId)
      // 本地清除未读数
      set((state) => ({
        channels: state.channels.map((ch) =>
          ch.channel_id === channelId ? { ...ch, unread_count: 0 } : ch,
        ),
      }))
    } catch {
      // 降级：忽略
    }
  },

  uploadFile: async (channelId, file) => {
    set({ sending: true })
    const userId = currentUserId || 'me'
    // 乐观追加文件消息
    const optimisticMessage: ChatMessage = {
      message_id: Date.now(),
      channel_id: channelId,
      sender_id: userId,
      sender_type: 'user',
      sender_name: '我',
      content: `已上传文件: ${file.name}`,
      message_type: 'file',
      file_info: {
        file_id: '',
        filename: file.name,
        file_size: file.size,
        mime_type: file.type,
      },
      created_at: new Date().toISOString(),
    }
    set((state) => ({ messages: [...state.messages, optimisticMessage] }))

    try {
      const res = await apiService.uploadChatFile(channelId, file)
      // 如果后端返回了消息，替换乐观消息
      if (res && res.message_id) {
        set((state) => ({
          messages: state.messages.map((m) =>
            m.message_id === optimisticMessage.message_id
              ? {
                  ...m,
                  message_id: res.message_id,
                  file_info: res.file_info ?? m.file_info,
                  content: res.content ?? m.content,
                }
              : m,
          ),
        }))
      }
    } catch {
      // 保留乐观消息，标记可能失败
    } finally {
      set({ sending: false })
    }
  },

  setActiveChannel: (channelId) => {
    set({ activeChannelId: channelId, messages: [] })
    get().fetchMessages(channelId)
    get().markAsRead(channelId)
  },

  receiveMessage: (message) => {
    const state = get()
    const activeChannelId = state.activeChannelId

    // 如果消息属于当前活跃频道，追加到 messages
    if (message.channel_id === activeChannelId) {
      // 避免重复
      const exists = state.messages.some((m) => m.message_id === message.message_id)
      if (!exists) {
        set((s) => ({ messages: [...s.messages, message] }))
      }
    }

    // 更新频道列表中的最后消息预览和未读数
    set((s) => ({
      channels: s.channels.map((ch) => {
        if (ch.channel_id !== message.channel_id) return ch
        const isSelf = message.sender_id === currentUserId
        return {
          ...ch,
          last_message_preview:
            message.message_type === 'file' ? `[文件] ${message.file_info?.filename ?? ''}` : message.content,
          last_message_at: message.created_at,
          unread_count: message.channel_id === activeChannelId || isSelf ? ch.unread_count : ch.unread_count + 1,
        }
      }),
    }))

    // 如果频道不存在于列表中（可能是新频道），刷新频道列表
    const channelExists = state.channels.some((ch) => ch.channel_id === message.channel_id)
    if (!channelExists) {
      get().fetchChannels()
    }
  },

  disconnect: () => {
    currentToken = null
    currentUserId = null
    reconnectAttempts = 0
    clearHeartbeat()
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (wsInstance) {
      wsInstance.onopen = null
      wsInstance.onmessage = null
      wsInstance.onerror = null
      wsInstance.onclose = null
      if (wsInstance.readyState === WebSocket.OPEN || wsInstance.readyState === WebSocket.CONNECTING) {
        wsInstance.close()
      }
      wsInstance = null
    }
    set({
      channels: [],
      activeChannelId: null,
      messages: [],
      onlineUsers: [],
      wsConnected: false,
    })
  },
}))
