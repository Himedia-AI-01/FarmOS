import { useQuery } from '@tanstack/react-query';
import api from '@/lib/api';
import type { ChatLog, ChatSession } from '@/admin/types/chatlog';

export function useChatLogs(filters: { intent?: string; escalated?: boolean } = {}) {
  const params: Record<string, string> = {};
  if (filters.intent) params.intent = filters.intent;
  if (filters.escalated !== undefined) params.escalated = String(filters.escalated);

  return useQuery<ChatLog[]>({
    queryKey: ['admin-chat-logs', filters],
    queryFn: async () => {
      const { data } = await api.get('/api/admin/chatbot/logs', { params });
      return data;
    },
  });
}

export function useEscalatedChatLogs() {
  return useQuery<ChatLog[]>({
    queryKey: ['admin-chat-logs-escalated'],
    queryFn: async () => {
      const { data } = await api.get('/api/admin/chatbot/logs/escalated');
      return data;
    },
  });
}

export function useChatSessions(escalatedOnly = false) {
  return useQuery<ChatSession[]>({
    queryKey: ['admin-chat-sessions', { escalatedOnly }],
    queryFn: async () => {
      const { data } = await api.get('/api/admin/chatbot/sessions', {
        params: escalatedOnly ? { escalated_only: true } : {},
      });
      return data;
    },
  });
}

export function useSessionLogs(sessionId: number | null) {
  return useQuery<ChatLog[]>({
    queryKey: ['admin-session-logs', sessionId],
    queryFn: async () => {
      const { data } = await api.get(`/api/admin/chatbot/sessions/${sessionId}/logs`);
      return data;
    },
    enabled: sessionId != null,
  });
}
