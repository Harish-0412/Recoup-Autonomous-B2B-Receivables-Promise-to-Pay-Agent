import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchTaskStatus } from '@/lib/api';
import { motion } from 'framer-motion';

interface BadgeProps {
  label: string;
  active: boolean;
  tooltip?: string;
}

const Badge: React.FC<BadgeProps> = ({ label, active, tooltip }) => (
  <motion.span
    className={`px-2 py-0.5 rounded-full text-xs font-medium mr-2 ${
      active ? 'bg-emerald-600 text-white' : 'bg-amber-500 text-white'
    }`}
    whileHover={{ scale: 1.05 }}
    title={tooltip}
    layout
  >
    {label}
  </motion.span>
);

export default function StatusStrip() {
  const { data, isLoading } = useQuery(['task-status'], fetchTaskStatus, {
    refetchInterval: 30_000,
    staleTime: 10_000,
  });

  if (isLoading || !data) {
    return null;
  }

  const {
    dry_run,
    sending_enabled,
    use_model_scorer,
    open_invoices,
    pending_promises,
    replies_awaiting_review,
  } = data;

  return (
    <div className="flex items-center bg-gray-800 text-gray-100 px-4 py-1 space-x-2 overflow-x-auto">
      <Badge label={dry_run ? 'DRY_RUN' : 'LIVE'} active={dry_run} tooltip="DRY_RUN mode prevents real actions" />
      <Badge label={sending_enabled ? 'Sending: ON' : 'Sending: OFF'} active={sending_enabled} tooltip="Whether outbound messages are actually sent" />
      <Badge label={use_model_scorer ? 'Model scorer: ML' : 'Model scorer: rules'} active={use_model_scorer} />
      {open_invoices !== undefined && (
        <Badge label={`${open_invoices} open invoices`} active={open_invoices > 0} />
      )}
      {pending_promises !== undefined && (
        <Badge label={`${pending_promises} promises`} active={pending_promises > 0} />
      )}
      {replies_awaiting_review !== undefined && (
        <Badge label={`${replies_awaiting_review} replies need you`} active={replies_awaiting_review > 0} />
      )}
    </div>
  );
}
