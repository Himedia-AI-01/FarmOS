export type ShipmentStatus = 'registered' | 'picked_up' | 'in_transit' | 'delivered';

export interface RelatedTicket {
  id: number;
  action_type: 'cancel' | 'exchange';
  status: string;
  reason: string;
}

/** /api/admin/shipments 응답 — snake_case, 연관 티켓 포함 */
export interface AdminShipment {
  id: number;
  order_id: number;
  carrier: string;
  tracking_number: string;
  status: ShipmentStatus;
  expected_arrival: string | null;
  last_checked_at: string | null;
  delivered_at: string | null;
  tracking_history: string | null;
  created_at: string | null;
  order_total: number | null;
  /** 명시적 FK — null이면 원본 배송, non-null이면 이 배송 자체가 교환 배송 */
  related_ticket_id: number | null;
  related_ticket: RelatedTicket | null;
}

/** /api/shipments POST 페이로드 */
export interface ShipmentCreatePayload {
  order_id: number;
  carrier: string;
  tracking_number: string;
  expected_arrival?: string;
  related_ticket_id?: number;
}

export const SHIPMENT_STATUS_LABEL: Record<ShipmentStatus, string> = {
  registered: '등록됨',
  picked_up: '집화',
  in_transit: '배송중',
  delivered: '배송완료',
};

export const SHIPMENT_STATUS_COLOR: Record<ShipmentStatus, string> = {
  registered: 'bg-gray-100 text-gray-600',
  picked_up: 'bg-blue-100 text-blue-700',
  in_transit: 'bg-amber-100 text-amber-700',
  delivered: 'bg-green-100 text-green-700',
};

export const SHIPMENT_STATUS_STEP: ShipmentStatus[] = [
  'registered',
  'picked_up',
  'in_transit',
  'delivered',
];
