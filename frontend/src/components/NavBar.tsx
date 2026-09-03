import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';

const navLinks = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/queue', label: 'Queue' },
  { href: '/inbox', label: 'Inbox' },
  { href: '/policy', label: 'Policy' },
  { href: '/reports/batch', label: 'Analytics' },
  { href: '/runs', label: 'Runs' },
  { href: '/models/recovery', label: 'Models' },
  { href: '/simulate', label: 'Simulate' },
];

export default function NavBar() {
  const pathname = usePathname();

  return (
    <nav className="bg-neutral-800 text-white px-4 py-2 flex space-x-4">
      {navLinks.map((link) => (
        <Link
          key={link.href}
          href={link.href}
          className={cn(
            'hover:underline',
            pathname === link.href ? 'font-bold underline' : ''
          )}
        >
          {link.label}
        </Link>
      ))}
    </nav>
  );
}
