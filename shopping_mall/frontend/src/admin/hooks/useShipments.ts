import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import api from '@/lib/api';
import type { AdminShipment, ShipmentCreatePayload } from '@/admin/types/shipment';

export function useAdminShipments(status?: string) {
  const params = status ? { status } : {};
  return useQuery<AdminShipment[]>({
    queryKey: ['admin-shipments', status],
    queryFn: async () => {
      const { data } = await api.get('/api/admin/shipments', { params });
      return data;
    },
  });
}

export function useCheckShipment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (shipmentId: number) => {
      const { data } = await api.post(`/api/admin/shipments/${shipmentId}/check`);
      return data as AdminShipment;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-shipments'] });
    },
  });
}

export function useCreateShipment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ShipmentCreatePayload) => {
      const { data } = await api.post('/api/shipments', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-shipments'] });
    },
  });
}
