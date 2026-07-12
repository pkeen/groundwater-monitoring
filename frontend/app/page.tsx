"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import SearchBar from "@/components/SearchBar";
import SitePanel from "@/components/SitePanel";
import { fetchSites, searchPostcode, type SiteSummary } from "@/lib/api";

const GroundwaterMap = dynamic(() => import("@/components/GroundwaterMap"), { ssr: false });

export default function Home() {
  const [allSites, setAllSites] = useState<SiteSummary[]>([]);
  const [visibleSites, setVisibleSites] = useState<SiteSummary[]>([]);
  const [selected, setSelected] = useState<SiteSummary | null>(null);
  const [flyTo, setFlyTo] = useState<[number, number] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchNote, setSearchNote] = useState<string | null>(null);

  useEffect(() => {
    fetchSites()
      .then((sites) => {
        setAllSites(sites);
        setVisibleSites(sites);
      })
      .catch(() => setSearchError("Could not load sites from the API."));
  }, []);

  async function handleNameSearch(query: string) {
    setSearching(true);
    setSearchError(null);
    setSearchNote(null);
    try {
      const results = allSites.filter((s) => s.label.toLowerCase().includes(query.toLowerCase()));
      setVisibleSites(results);
      if (results.length > 0) {
        setFlyTo([results[0].lat, results[0].lon]);
        setSelected(results[0]);
      } else {
        setSearchError(`No sites found matching "${query}".`);
      }
    } finally {
      setSearching(false);
    }
  }

  async function handlePostcodeSearch(postcode: string) {
    setSearching(true);
    setSearchError(null);
    setSearchNote(null);
    try {
      const result = await searchPostcode(postcode);
      setVisibleSites(result.sites);
      setFlyTo([result.lat, result.lon]);
      setSearchNote(`${result.sites.length} sites within 15km of ${result.postcode}`);
      if (result.sites.length === 0) {
        setSearchError(`No groundwater sites found near ${result.postcode}.`);
      }
    } catch {
      setSearchError(`Could not find postcode "${postcode}".`);
    } finally {
      setSearching(false);
    }
  }

  function resetSearch() {
    setVisibleSites(allSites);
    setSearchError(null);
    setSearchNote(null);
  }

  const counts = useMemo(() => {
    const level = visibleSites.filter((s) => s.type === "level").length;
    const quality = visibleSites.filter((s) => s.type === "quality").length;
    return { level, quality };
  }, [visibleSites]);

  return (
    <div className="flex h-screen w-screen flex-col">
      <header className="z-10 flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 bg-white px-4 py-3 shadow-sm">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">England Groundwater Monitoring</h1>
          <p className="text-xs text-gray-500">
            {counts.level} level stations &middot; {counts.quality} quality sampling points
            {visibleSites.length !== allSites.length && (
              <button onClick={resetSearch} className="ml-2 text-blue-600 underline">
                reset
              </button>
            )}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <SearchBar onNameSearch={handleNameSearch} onPostcodeSearch={handlePostcodeSearch} loading={searching} />
          {searchNote && <p className="text-xs text-gray-500">{searchNote}</p>}
          {searchError && <p className="text-xs text-red-600">{searchError}</p>}
        </div>
      </header>

      <div className="relative flex flex-1 overflow-hidden">
        <div className="flex-1">
          <GroundwaterMap
            sites={visibleSites}
            selectedId={selected?.id ?? null}
            onSelect={setSelected}
            flyToCenter={flyTo}
          />
        </div>
        {selected && (
          <div className="w-96 shrink-0 border-l border-gray-200 bg-white shadow-lg">
            <SitePanel site={selected} onClose={() => setSelected(null)} />
          </div>
        )}
      </div>
    </div>
  );
}
