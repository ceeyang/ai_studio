import { createContext, type ReactNode, useContext } from 'react';
import { type EventsState, initialEvents } from './events';
import { useStudioEvents } from './useStudioEvents';

/** 全局只连一条 /api/ws。测试里可以直接用 EventsContext.Provider 注入状态。 */
export const EventsContext = createContext<EventsState>(initialEvents);

export function EventsProvider({ children }: { children: ReactNode }) {
  const state = useStudioEvents();
  return <EventsContext.Provider value={state}>{children}</EventsContext.Provider>;
}

export function useEvents(): EventsState {
  return useContext(EventsContext);
}
