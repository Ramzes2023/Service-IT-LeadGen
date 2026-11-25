from leadpilot.serviceit_evidence_stage_handoff import target,INN,COMPANY,REPORT,BRIEF
M={'new':'NEW','company':'INN','research':'RESEARCH','manager':'MANAGER'}
def card():return {'stageId':'NEW',INN:'7707083893',COMPANY:'Компания',REPORT:'факты '*100,BRIEF:'досье '*100}
def test_complete_research_needs_no_fns():assert target(card(),'VERIFIED',True,M)=='MANAGER'
def test_completed_unsuccessful_contact_search_goes_to_human():assert target(card(),'NOT_FOUND',True,M)=='MANAGER'
def test_pending_documents_stay_in_research():assert target(card(),'VERIFIED',False,M)=='RESEARCH'
def test_pending_search_stays_in_research():assert target(card(),None,True,M)=='RESEARCH'
def test_missing_inn_goes_to_company_check():assert target({**card(),INN:''},None,False,M)=='INN'
def test_manager_stages_are_not_overwritten():
 for s in ['MANAGER','SUCCESS','FAIL','CUSTOM']:assert target({**card(),'stageId':s},'VERIFIED',True,M) is None
