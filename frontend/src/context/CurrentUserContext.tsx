import { createContext, useContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

export interface CurrentUserOption {
  employee_no: string;
  name: string;
  department: string;
}

/** Demo 身份：仅用于职场页的数据视角切换，无真实登录。 */
export const CURRENT_USERS: CurrentUserOption[] = [
  { employee_no: 'E10281', name: '张三', department: '架构部' },
  { employee_no: 'E20999', name: '陈晓萌', department: '研发部' },
];

const STORAGE_KEY = 'dwp.current-actor';

interface CurrentUserContextValue {
  actor: CurrentUserOption;
  setActor: (employeeNo: string) => void;
}

const CurrentUserContext = createContext<CurrentUserContextValue | null>(null);

export function CurrentUserProvider({ children }: { children: ReactNode }) {
  const [employeeNo, setEmployeeNo] = useState<string>(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved && CURRENT_USERS.some((user) => user.employee_no === saved) ? saved : CURRENT_USERS[0].employee_no;
  });

  const actor = useMemo(
    () => CURRENT_USERS.find((user) => user.employee_no === employeeNo) ?? CURRENT_USERS[0],
    [employeeNo],
  );

  const value = useMemo<CurrentUserContextValue>(
    () => ({
      actor,
      setActor: (no: string) => {
        setEmployeeNo(no);
        localStorage.setItem(STORAGE_KEY, no);
      },
    }),
    [actor],
  );

  return <CurrentUserContext.Provider value={value}>{children}</CurrentUserContext.Provider>;
}

export function useCurrentUser(): CurrentUserContextValue {
  const ctx = useContext(CurrentUserContext);
  if (!ctx) {
    throw new Error('useCurrentUser 必须在 CurrentUserProvider 内使用');
  }
  return ctx;
}
