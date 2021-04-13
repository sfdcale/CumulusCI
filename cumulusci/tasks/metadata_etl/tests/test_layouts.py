from cumulusci.tasks.salesforce.tests.util import create_task
from unittest.mock import Mock
import unittest

from cumulusci.tasks.metadata_etl import AddRelatedLists
from cumulusci.tasks.metadata_etl.layouts import AddRecordPlatformActionListItem
from cumulusci.utils.xml import metadata_tree


MD = "{%s}" % metadata_tree.METADATA_NAMESPACE


LAYOUT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Layout xmlns="http://soap.sforce.com/2006/04/metadata">
    <layoutSections>
        <customLabel>false</customLabel>
        <detailHeading>false</detailHeading>
        <editHeading>true</editHeading>
        <label>Information</label>
        <layoutColumns>
            <layoutItems>
                <behavior>Readonly</behavior>
                <field>Name</field>
            </layoutItems>
        </layoutColumns>
        <layoutColumns/>
        <style>TwoColumnsTopToBottom</style>
    </layoutSections>
    {relatedLists}
</Layout>
"""

RELATED_LIST = """    <relatedLists>
        <fields>FULL_NAME</fields>
        <fields>CONTACT.TITLE</fields>
        <fields>CONTACT.EMAIL</fields>
        <fields>CONTACT.PHONE1</fields>
        <relatedList>RelatedContactList</relatedList>
    </relatedLists>
"""


class TestAddRelatedLists:
    def test_adds_related_list(self):
        task = create_task(
            AddRelatedLists,
            {
                "managed": True,
                "api_version": "47.0",
                "api_names": "bar,foo",
                "related_list": "TEST",
                "fields": "foo__c,bar__c",
            },
        )

        tree = metadata_tree.fromstring(
            LAYOUT_XML.format(relatedLists=RELATED_LIST).encode("utf-8")
        )
        element = tree._element

        assert len(element.findall(f".//{MD}relatedLists[{MD}relatedList='TEST']")) == 0

        task._transform_entity(tree, "Layout")

        assert len(element.findall(f".//{MD}relatedLists[{MD}relatedList='TEST']")) == 1
        field_elements = element.findall(
            f".//{MD}relatedLists[{MD}relatedList='TEST']/{MD}fields"
        )
        field_names = {elem.text for elem in field_elements}
        assert field_names == set(["foo__c", "bar__c"])

    def test_excludes_buttons(self):
        task = create_task(
            AddRelatedLists,
            {
                "managed": True,
                "api_version": "47.0",
                "api_names": "bar,foo",
                "related_list": "TEST",
                "fields": "foo__c,bar__c",
                "exclude_buttons": "New,Edit",
            },
        )

        tree = metadata_tree.fromstring(
            LAYOUT_XML.format(relatedLists=RELATED_LIST).encode("utf-8")
        )

        assert (
            len(tree._element.findall(f".//{MD}relatedLists[{MD}relatedList='TEST']"))
            == 0
        )

        result = task._transform_entity(tree, "Layout")

        assert (
            len(result._element.findall(f".//{MD}relatedLists[{MD}relatedList='TEST']"))
            == 1
        )
        button_elements = result._element.findall(
            f".//{MD}relatedLists[{MD}relatedList='TEST']/{MD}excludeButtons"
        )
        excluded_buttons = {elem.text for elem in button_elements}
        assert excluded_buttons == set(["New", "Edit"])

    def test_includes_buttons(self):
        task = create_task(
            AddRelatedLists,
            {
                "managed": True,
                "api_version": "47.0",
                "api_names": "bar,foo",
                "related_list": "TEST",
                "fields": "foo__c,bar__c",
                "custom_buttons": "MyCustomNewAction,MyCustomEditAction",
            },
        )

        tree = metadata_tree.fromstring(
            LAYOUT_XML.format(relatedLists=RELATED_LIST).encode("utf-8")
        )

        assert (
            len(tree._element.findall(f".//{MD}relatedLists[{MD}relatedList='TEST']"))
            == 0
        )

        result = task._transform_entity(tree, "Layout")
        element = result._element

        assert len(element.findall(f".//{MD}relatedLists[{MD}relatedList='TEST']")) == 1
        button_elements = element.findall(
            f".//{MD}relatedLists[{MD}relatedList='TEST']/{MD}customButtons"
        )
        custom_buttons = {elem.text for elem in button_elements}
        assert custom_buttons == set(["MyCustomNewAction", "MyCustomEditAction"])

    def test_adds_related_list_no_existing(self):
        task = create_task(
            AddRelatedLists,
            {
                "managed": True,
                "api_version": "47.0",
                "api_names": "bar,foo",
                "related_list": "TEST",
                "fields": "foo__c,bar__c",
            },
        )

        tree = metadata_tree.fromstring(
            LAYOUT_XML.format(relatedLists="").encode("utf-8")
        )
        element = tree._element

        assert len(element.findall(f".//{MD}relatedLists[{MD}relatedList='TEST']")) == 0

        task._transform_entity(tree, "Layout")

        assert len(element.findall(f".//{MD}relatedLists[{MD}relatedList='TEST']")) == 1
        field_elements = element.findall(
            f".//{MD}relatedLists[{MD}relatedList='TEST']/{MD}fields"
        )
        field_names = {elem.text for elem in field_elements}
        assert field_names == set(["foo__c", "bar__c"])

    def test_skips_existing_related_list(self):
        task = create_task(
            AddRelatedLists,
            {
                "managed": True,
                "api_version": "47.0",
                "api_names": "bar,foo",
                "related_list": "RelatedContactList",
                "fields": "foo__c,bar__c",
            },
        )

        tree = metadata_tree.fromstring(
            LAYOUT_XML.format(relatedLists=RELATED_LIST).encode("utf-8")
        )

        result = task._transform_entity(tree, "Layout")

        assert result is None


##### Lightning Action add to Layout tests

# SET MOCK DATA

# Mocked empty page layout (no action list)
#   Included common elements to better emulate a 'real' page layout.
MOCK_EMPTY_LAYOUT = """<?xml version="1.0" encoding="UTF-8"?>
<Layout xmlns="http://soap.sforce.com/2006/04/metadata">
    <excludeButtons>Submit</excludeButtons>
    <layoutSections>
        <customLabel>false</customLabel>
        <detailHeading>false</detailHeading>
        <editHeading>true</editHeading>
        <label>Information</label>
        <layoutColumns>
            <layoutItems>
                <behavior>Required</behavior>
                <field>Name</field>
            </layoutItems>
            <layoutItems>
                <emptySpace>true</emptySpace>
            </layoutItems>
            <layoutItems>
                <emptySpace>true</emptySpace>
            </layoutItems>
        </layoutColumns>
        <style>TwoColumnsTopToBottom</style>
    </layoutSections>
    <miniLayout>
        <fields>Name</fields>
        <relatedLists>
            <fields>NAME</fields>
            <fields>STATUS</fields>            
            <relatedList>MOCKOBJECT</relatedList>
        </relatedLists>
    </miniLayout>
    <relatedLists>
        <fields>FULL_NAME</fields>
        <fields>CONTACT.TITLE</fields>
        <fields>CONTACT.EMAIL</fields>
        <fields>CONTACT.PHONE1</fields>
        <relatedList>RelatedContactList</relatedList>
    </relatedLists>
    <relatedLists>
        <relatedList>RelatedFileList</relatedList>
    </relatedLists>
    <showEmailCheckbox>false</showEmailCheckbox>
    <showHighlightsPanel>false</showHighlightsPanel>
    <showInteractionLogPanel>false</showInteractionLogPanel>
    <showRunAssignmentRulesCheckbox>false</showRunAssignmentRulesCheckbox>
    <showSubmitAndAttachButton>false</showSubmitAndAttachButton>
    {action_list_scenario}
</Layout>
"""

# Mocked existing action list
#   For different scenarios change action_list_context (Record, Listview, etc)
#   and optional_last_action_item for inserting an existing item
MOCK_EXISTING_ACTION_LIST = """
<platformActionList>
    <actionListContext>{action_list_context}</actionListContext>
    {optional_first_action_item}
    <platformActionListItems>
        <actionName>Edit</actionName>
        <actionType>StandardButton</actionType>
        <sortOrder>{}</sortOrder>
    </platformActionListItems>
    <platformActionListItems>
        <actionName>FeedItem.TextPost</actionName>
        <actionType>QuickAction</actionType>
        <sortOrder>{}</sortOrder>
    </platformActionListItems>     
    {optional_last_action_item}
</platformActionList>
"""

EMPTY_ACTION_ITEM = """
    <platformActionListItems>
        <actionName>{action_name}</actionName>
        <actionType>{action_type}</actionType>
        <sortOrder>{expected_order}</sortOrder>
    </platformActionListItems>
"""
EMPTY_ACTION_LIST = """
<platformActionList>
    <actionListContext>{action_list_context}</actionListContext>
    {optional_action_item}
</platformActionList>
"""


class TestAddRecordPlatformActionListItem:
    def test_adds_action_item_to_existing_list_place_last(self):
        # options scenario:
        #   adding Quick Action to layout with existing action item list
        #   not setting place_first, so should be last in action list
        options = {
            "action_type": "QuickAction",
            "action_name": "pkg__mockObject.TestQuickAction",
        }
        task = create_task(AddRecordPlatformActionListItem, options)
        assert (
            task._place_first == False
        ), " task._place_first should be false when not setting `place_first` task_option"
        ### MOCK DATA
        # build our existing action list and create our metadata tree
        #   "Record" context
        #   default sort order (0, 1)
        #   no optional
        mock_action_list = MOCK_EXISTING_ACTION_LIST.format(
            0,
            1,
            action_list_context="Record",
            optional_first_action_item="",
            optional_last_action_item="",
        )
        metadata = metadata_tree.fromstring(
            MOCK_EMPTY_LAYOUT.format(action_list_scenario=mock_action_list).encode(
                "utf-8"
            )
        )
        mock_action_list_size = len(
            metadata._get_child("platformActionList").findall("platformActionListItems")
        )

        # Creating expected action item/list xml and metadata
        #   Context  = "Record"
        #   our action_list_items <sortOrder> (positional *args) can be dynamically set from the mock_action_list_size range
        #   using our optional_last_action_item to set our expected_action_item placement
        expected_action_item = EMPTY_ACTION_ITEM.format(
            expected_order=mock_action_list_size, **options
        )
        expected_action_list = MOCK_EXISTING_ACTION_LIST.format(
            *tuple([n for n in range(mock_action_list_size)]),
            action_list_context="Record",
            optional_first_action_item="",
            optional_last_action_item=expected_action_item,
        )
        expected_metadata = metadata_tree.fromstring(
            MOCK_EMPTY_LAYOUT.format(action_list_scenario=expected_action_list).encode(
                "utf-8"
            )
        )

        # run test actual
        actual_metadata = task._transform_entity(metadata, "Layout")
        # Assert our transformed metadata is the same as our expected
        # This confirms, action list item size, sortOrder, and record context
        assert (
            actual_metadata.tostring() == expected_metadata.tostring()
        ), "The transformed actual_metadata should match our expected_metadata"

    def test_adds_action_item_to_existing_list_place_first(self):
        # options scenario:
        #   adding Quick Action to layout with existing action item list
        #   place_first = true, so should be first in action list
        options = {
            "action_type": "QuickAction",
            "action_name": "pkg__mockObject.TestQuickAction",
            "place_first": True,
        }
        task = create_task(AddRecordPlatformActionListItem, options)

        ### MOCK DATA
        # build our existing action list and create our metadata tree
        #   "Record" context
        #   default sort order (0, 1)
        #   no optional
        mock_action_list = MOCK_EXISTING_ACTION_LIST.format(
            0,
            1,
            action_list_context="Record",
            optional_first_action_item="",
            optional_last_action_item="",
        )
        metadata = metadata_tree.fromstring(
            MOCK_EMPTY_LAYOUT.format(action_list_scenario=mock_action_list).encode(
                "utf-8"
            )
        )
        mock_action_list_size = len(
            metadata._get_child("platformActionList").findall("platformActionListItems")
        )

        # Creating expected action item/list xml and metadata
        #   Context  = "Record"
        #   our action_list_items <sortOrder> (positional *args) can be dynamically set from a tuple the mock_action_list_size range
        #       setting our added item to 0 sortOrder since placement should be first
        #   using our optional_first_action_item to set our expected_action_item placement
        expected_action_item = EMPTY_ACTION_ITEM.format(expected_order=0, **options)
        expected_action_list = MOCK_EXISTING_ACTION_LIST.format(
            *tuple([n for n in range(1, mock_action_list_size + 1)]),
            action_list_context="Record",
            optional_first_action_item=expected_action_item,
            optional_last_action_item="",
        )
        expected_metadata = metadata_tree.fromstring(
            MOCK_EMPTY_LAYOUT.format(action_list_scenario=expected_action_list).encode(
                "utf-8"
            )
        )

        # run test actual
        actual_metadata = task._transform_entity(metadata, "Layout")
        # Assert our transformed metadata is the same as our expected
        # This confirms, action list item size, sortOrder, and record context
        assert (
            actual_metadata.tostring() == expected_metadata.tostring()
        ), "The transformed actual_metadata should match our expected_metadata"

    def test_does_not_add_action_if_already_exists(self):
        # options scenario:
        #   attempting to add Quick Action to layout with quick action already existing
        options = {
            "action_type": "QuickAction",
            "action_name": "pkg__mockObject.TestQuickAction",
        }
        task = create_task(AddRecordPlatformActionListItem, options)

        ### MOCK DATA
        # build our existing action list and create our metadata tree
        #   "Record" context
        #   default sort order (0, 1)
        mock_action_item = EMPTY_ACTION_ITEM.format(expected_order=2, **options)
        mock_action_list = MOCK_EXISTING_ACTION_LIST.format(
            0,
            1,
            action_list_context="Record",
            optional_first_action_item="",
            optional_last_action_item=mock_action_item,
        )
        metadata = metadata_tree.fromstring(
            MOCK_EMPTY_LAYOUT.format(action_list_scenario=mock_action_list).encode(
                "utf-8"
            )
        )

        # run test
        actual_metadata = task._transform_entity(metadata, "Layout")
        # should not transform
        assert (
            not actual_metadata
        ), "AddRecordPlatformActionListItem should return None when actionListItem already exists in metadata"

    def test_creates_new_action_list_when_none_present(self):
        # options scenario:
        #   adding Quick Action to layout without existing action list
        #   place_first = true, so should be first in action list
        options = {
            "action_type": "QuickAction",
            "action_name": "pkg__mockObject.TestQuickAction",
            "place_first": True,
        }
        task = create_task(AddRecordPlatformActionListItem, options)

        ### MOCK DATA
        # build our existing action list and create our metadata tree
        #   "Record" context
        #   default sort order (0, 1)
        #   no optional
        metadata = metadata_tree.fromstring(
            MOCK_EMPTY_LAYOUT.format(action_list_scenario="").encode("utf-8")
        )

        # Creating expected action item/list xml and metadata
        #   Context  = "Record"
        #   Should only contain one action item
        expected_action_item = EMPTY_ACTION_ITEM.format(expected_order=0, **options)
        expected_action_list = EMPTY_ACTION_LIST.format(
            action_list_context="Record", optional_action_item=expected_action_item
        )
        expected_metadata = metadata_tree.fromstring(
            MOCK_EMPTY_LAYOUT.format(action_list_scenario=expected_action_list).encode(
                "utf-8"
            )
        )

        # run test actual
        actual_metadata = task._transform_entity(metadata, "Layout")
        # Assert our transformed metadata is the same as our expected
        # This confirms, action list item size, sortOrder, and record context
        assert (
            actual_metadata.tostring() == expected_metadata.tostring()
        ), "The transformed actual_metadata should match our expected_metadata - and created a list"


#   LAYOUT with non record platform action
