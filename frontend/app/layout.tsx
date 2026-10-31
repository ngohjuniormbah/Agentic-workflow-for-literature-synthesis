import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title:'Synthesis — Literature Review', description:'Human supervised agentic literature synthesis' };
export default function RootLayout({children}:{children:React.ReactNode}) { return <html lang="en"><body>{children}</body></html> }
