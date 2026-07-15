import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MedDocs AI",
  description: "Multi-tenant medical document processing platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen bg-gray-50">
          <nav className="bg-white shadow-sm border-b">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
              <div className="flex justify-between h-16 items-center">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 bg-primary-600 rounded-lg flex items-center justify-center">
                    <span className="text-white font-bold text-sm">MD</span>
                  </div>
                  <h1 className="text-xl font-semibold text-gray-900">MedDocs AI</h1>
                </div>
                <div className="flex gap-4">
                  <a href="/" className="text-gray-600 hover:text-gray-900 text-sm font-medium">Patients</a>
                  <a href="/upload" className="text-gray-600 hover:text-gray-900 text-sm font-medium">Upload</a>
                  <a href="/monitoring" className="text-gray-600 hover:text-gray-900 text-sm font-medium">Activity</a>
                </div>
              </div>
            </div>
          </nav>
          <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">{children}</main>
        </div>
      </body>
    </html>
  );
}
